"""
============================================================
Monitor de Finalizacao NAS — Automacao de Backups
============================================================
Versao: 1.0.0
Data:   2026-05-22
Descricao: A cada 30 min varre backups em status 'aguardando_nas'
           que ja passaram da janela de 23h (tempo dado ao NAS Synology
           para coletar o ZIP da pasta /mnt/hdd/sync_nas/) e executa
           incondicionalmente o ciclo de encerramento:

           - finalizar_backup(status='concluido')
           - comentar_sucesso no Jira
           - transicionar_resolvido(ticket_id)
           - deletar_conta no Workspace (se deletar_conta=True)
           - notificar Google Chat operacional
           - promover marker <X>.zip.ready -> <X>.zip.uploaded
             (libera a limpeza_zips_sincronizados a apagar o ZIP
              local apos NAS_SYNC_RETENCAO_DIAS dias)

           NAO ha verificacao externa (Drive/webhook). O servidor
           confia que o NAS coletou em ate 23h, conforme acordado
           com o cliente.
============================================================
Historico:
  1.0.0 (2026-05-22) — Versao inicial.
============================================================
"""

import threading
import time
from pathlib import Path

from utils.logger import obter_logger

logger = obter_logger("finalizacao_nas")

# Janela dada ao NAS para coletar o ZIP da sync_nas antes do servidor
# considerar consolidado e encerrar o ciclo.
_HORAS_ESPERA = 23

# Intervalo entre varreduras do banco em busca de backups maduros.
# 30 min e suficiente para que a latencia entre 23h e a finalizacao real
# fique no maximo em ~30 min (aceitavel para o caso de uso).
_INTERVALO_VARREDURA = 30 * 60

# Pequena espera no boot para estabilizacao de banco/redis/worker.
_ESPERA_INICIAL = 60


def finalizar_backups_pendentes() -> dict:
    """Varre backups com tempo > 23h em aguardando_nas e encerra o ciclo.

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
    from servicos.jira_atualizacao import comentar_sucesso, transicionar_resolvido
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

            # 2) Jira: comentario de sucesso + transicao para resolvido
            try:
                comentar_sucesso(ticket, email, link, deletar_conta=del_conta)
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

            # 5) Promove marker .ready -> .uploaded (libera limpeza posterior)
            _promover_marker(link)

            finalizados += 1
            logger.info(f"Finalizado #{b['id']} — {email} / {ticket}")
        except Exception as erro:
            logger.error(
                f"Erro inesperado ao finalizar backup #{b.get('id')} ({email}): {erro}",
                exc_info=True,
            )

    logger.info(f"Ciclo de finalizacao_nas concluido — finalizados={finalizados}")
    return {"finalizados": finalizados}


def _promover_marker(link_local: str) -> None:
    """Renomeia <X>.zip.ready -> <X>.zip.uploaded para liberar a limpeza.

    O link guardado e do formato 'nas:/mnt/hdd/sync_nas/...zip'. Strip do
    prefixo 'nas:' antes de mexer no filesystem. Se o marker nao existir
    (ja promovido ou nunca criado), apenas loga e segue.
    """
    if not link_local:
        return
    caminho = link_local[4:] if link_local.startswith("nas:") else link_local
    zip_path = Path(caminho)
    marker_ready = Path(str(zip_path) + ".ready")
    marker_uploaded = Path(str(zip_path) + ".uploaded")
    try:
        if marker_ready.exists():
            marker_ready.rename(marker_uploaded)
            logger.debug(f"Marker promovido: {marker_ready.name} -> {marker_uploaded.name}")
        elif marker_uploaded.exists():
            logger.debug(f"Marker ja estava promovido: {marker_uploaded.name}")
        else:
            logger.warning(
                f"Nenhum marker encontrado para {zip_path.name} — "
                f"limpeza_zips_sincronizados nao vai apagar o ZIP."
            )
    except OSError as erro:
        logger.error(f"Falha ao promover marker {marker_ready}: {erro}")


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
