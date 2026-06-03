"""
============================================================
Monitor de Finalizacao NAS — Automacao de Backups
============================================================
Versao: 1.0.0
Data:   2026-05-22
Descricao: A cada 30 min varre backups em status 'aguardando_nas'
           que ja passaram da janela de espera (default 6h, configuravel
           via NAS_SYNC_HORAS_ESPERA — tempo dado ao NAS Synology para
           coletar o ZIP da pasta /mnt/hdd/vault/sync_nas/) e executa
           incondicionalmente o ciclo de encerramento:

           - finalizar_backup(status='concluido')
           - comentar_sucesso no Jira
           - transicionar_resolvido(ticket_id)
           - deletar_conta no Workspace (se deletar_conta=True)
           - notificar Google Chat operacional
           - apagar o ZIP local de NAS_SYNC_DIR (o NAS ja teve a janela
             de espera para coletar; a copia local fica redundante)

           NAO ha verificacao externa (Drive/webhook). O servidor
           confia que o NAS coletou dentro da janela de espera,
           conforme acordado com o cliente.
============================================================
Historico:
  1.0.0 (2026-05-22) — Versao inicial.
============================================================
"""

import os
import threading
import time
from pathlib import Path

from utils.logger import obter_logger

logger = obter_logger("finalizacao_nas")

# Janela dada ao NAS para coletar o ZIP da sync_nas antes do servidor
# considerar consolidado e encerrar o ciclo. Configuravel via env
# NAS_SYNC_HORAS_ESPERA (default 6h) para ajuste sem rebuild.
_HORAS_ESPERA = int(os.getenv("NAS_SYNC_HORAS_ESPERA", "6"))

# Intervalo entre varreduras do banco em busca de backups maduros.
# 30 min e suficiente para que a latencia entre a janela e a finalizacao
# real fique no maximo em ~30 min (aceitavel para o caso de uso).
_INTERVALO_VARREDURA = 30 * 60

# Pequena espera no boot para estabilizacao de banco/redis/worker.
_ESPERA_INICIAL = 60


def finalizar_backups_pendentes() -> dict:
    """Varre backups maduros (> _HORAS_ESPERA) em aguardando_nas e encerra o ciclo.

    Cada finalizacao e idempotente: se ja foi finalizado por uma execucao
    anterior, o status ja sera 'concluido' e nao aparecera na lista
    retornada por listar_prontos_para_finalizar.

    Returns:
        dict com {"finalizados": int} para observabilidade.
    """
    # Imports locais para evitar custo no carregamento do modulo
    from dados.repositorio_backups import (
        listar_prontos_para_finalizar,
        finalizar_backup,
    )
    from servicos.jira_atualizacao import (
        comentar_sucesso,
        submeter_formularios_pendentes,
        transicionar_resolvido,
    )
    from servicos.conta_exclusao import deletar_conta
    from servicos.google_chat import (
        notificar_sucesso as chat_sucesso,
        notificar_erro_exclusao_conta,
    )

    pendentes = listar_prontos_para_finalizar(horas=_HORAS_ESPERA)
    if not pendentes:
        logger.info("Nenhum backup pronto para finalizar neste ciclo.")
        return {"finalizados": 0}

    logger.info(
        f"{len(pendentes)} backup(s) prontos para finalizar "
        f"(aguardando_nas ha > {_HORAS_ESPERA}h)"
    )

    finalizados = 0
    for b in pendentes:
        email     = b["email"]
        ticket    = b["ticket_id"]
        link      = b["link_drive"]
        nome      = b.get("nome")
        sha       = b.get("sha256_zip")
        del_conta = b["deletar_conta"]
        try:
            # 1) Atualiza DB primeiro — assim mesmo que Jira/chat falhem,
            #    o backup nao volta a aparecer na proxima varredura.
            finalizar_backup(email, sucesso=True, link_drive=link, sha256_zip=sha)

            # 2) Jira: comentario de sucesso + submissao dos formularios
            #    ProForma + transicao para resolvido. A tela de transicao
            #    "Resolvido" do SPN rejeita o POST (HTTP 400) se houver
            #    formularios anexados nao enviados — por isso submetemos antes,
            #    igual ao fluxo do orquestrador.
            try:
                comentar_sucesso(ticket, email, link, deletar_conta=del_conta)
                submeter_formularios_pendentes(ticket)
                transicionar_resolvido(ticket)
            except Exception as erro:
                logger.error(f"Falha ao atualizar Jira do ticket {ticket}: {erro}")

            # 3) Chat operacional
            try:
                chat_sucesso(email, ticket, link, nome, deletar_conta=del_conta)
            except Exception as erro:
                logger.warning(f"Falha ao notificar chat de sucesso ({email}): {erro}")

            # 4) Excluir conta Workspace (se solicitado). NAO ha verificacao
            #    no Drive — o usuario aceitou o risco.
            if del_conta:
                try:
                    deletar_conta(email)
                    logger.info(f"Conta Workspace excluida: {email}")
                except Exception as erro:
                    logger.error(f"Falha ao excluir conta {email}: {erro}")
                    try:
                        notificar_erro_exclusao_conta(email, ticket, str(erro)[:200], nome)
                    except Exception:
                        pass  # nao quebra o ciclo

            # 5) Apaga o ZIP local — a janela de espera ja passou, o NAS
            #    teve tempo de coletar e a copia local so ocupa disco.
            _apagar_zip_local(link)

            finalizados += 1
            logger.info(f"Finalizado #{b['id']} — {email} / {ticket}")
        except Exception as erro:
            logger.error(
                f"Erro inesperado ao finalizar backup #{b.get('id')} ({email}): {erro}",
                exc_info=True,
            )

    logger.info(f"Ciclo de finalizacao_nas concluido — finalizados={finalizados}")
    return {"finalizados": finalizados}


def _apagar_zip_local(link_local: str) -> None:
    """Apaga o ZIP local em NAS_SYNC_DIR apos a janela de espera do NAS.

    O link guardado tem o formato 'nas:/mnt/hdd/vault/sync_nas/...zip'. So apaga
    quando o destino foi o NAS (prefixo 'nas:') — no fallback Drive o link e uma
    URL https e nada deve ser apagado aqui. Erros sao logados e nao quebram o
    ciclo; a varredura limpar_zips_sincronizados serve de safety-net.
    """
    if not link_local or not link_local.startswith("nas:"):
        return
    zip_path = Path(link_local[4:])
    try:
        if zip_path.exists():
            tamanho_mb = zip_path.stat().st_size / (1024 * 1024)
            zip_path.unlink()
            logger.info(
                f"ZIP local apagado apos janela do NAS: {zip_path.name} ({tamanho_mb:.1f} MB)"
            )
        else:
            logger.debug(f"ZIP local ja nao existe (apagado antes?): {zip_path}")
    except OSError as erro:
        logger.error(f"Falha ao apagar ZIP local {zip_path}: {erro}")


# ── Monitor periodico (thread daemon) ───────────────────────────────────

_thread_iniciada = False
_lock = threading.Lock()


def _ciclo_monitor() -> None:
    """Loop principal: aguarda 60s pos-boot e roda finalizar_backups_pendentes
    a cada _INTERVALO_VARREDURA segundos. Excecoes do ciclo sao logadas e nao
    derrubam a thread.
    """
    time.sleep(_ESPERA_INICIAL)
    while True:
        try:
            finalizar_backups_pendentes()
        except Exception as erro:
            logger.error(f"Erro no ciclo de finalizacao_nas: {erro}", exc_info=True)
        time.sleep(_INTERVALO_VARREDURA)


def iniciar_monitor_finalizacao_nas() -> None:
    """Sobe a thread daemon de finalizacao NAS.

    Idempotente: chamadas subsequentes sao no-op (garante que so 1 thread
    monitora, mesmo se Flask recarregar o modulo).
    """
    global _thread_iniciada
    with _lock:
        if _thread_iniciada:
            return
        threading.Thread(
            target=_ciclo_monitor, daemon=True, name="finalizacao_nas"
        ).start()
        _thread_iniciada = True
        logger.info(
            f"Monitor de finalizacao NAS iniciado "
            f"(espera={_HORAS_ESPERA}h, varredura={_INTERVALO_VARREDURA}s)"
        )
