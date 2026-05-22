"""
============================================================
Módulo Orquestrador — Automação de Backups
============================================================
Versão: 2.0.0
Data: 2026-04-02
Descrição: Coordena todo o fluxo de backup de um colaborador
           desligado. Executa 8 etapas em sequência.

           A partir da v2.0.0, a execução assíncrona é
           gerenciada pelo Celery (broker Redis) em vez de
           threads locais.
============================================================
Histórico:
  2.0.0 (2026-04-02) — Celery, SHA256, exceções específicas,
                        verificação de duplicata via DB
  1.1.0 (2026-03-10) — Adicionada Etapa 8: exclusão da conta
  1.0.0 (2026-02-19) — Versão inicial
============================================================
"""

import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from servicos.vault_exportacao import (
    criar_exportacao_email,
    criar_exportacao_drive,
    monitorar_exportacao,
    baixar_exportacao,
    liberar_semaforo_exportacao,
)
from servicos.drive_upload import fazer_upload
from servicos.nas_sync import disponibilizar_para_nas, ErroNasSync
from servicos.conta_exclusao import verificar_e_deletar_conta
from servicos.jira_atualizacao import (
    comentar_inicio,
    comentar_progresso,
    comentar_sucesso,
    comentar_conta_excluida,
    transicionar_ticket,
    transicionar_resolvido,
    transicionar_para_status,
    submeter_formularios_pendentes,
)
from config.configuracoes import PASTA_VAULT
from processamento.compactacao import compactar_arquivos
from processamento.limpeza import limpar_arquivos_temporarios, limpar_arquivo_zip
from processamento.rastreador import (
    registrar_backup,
    atualizar_etapa,
    atualizar_progresso,
    finalizar_backup,
    STATUS_EM_ANDAMENTO,
    STATUS_CONCLUIDO,
    STATUS_ERRO,
)
from servicos.google_chat import (
    notificar_inicio as chat_notificar_inicio,
    notificar_sucesso as chat_notificar_sucesso,
    notificar_erro as chat_notificar_erro,
    notificar_conta_excluida as chat_notificar_conta_excluida,
    notificar_vault_reaproveitado as chat_notificar_vault_reaproveitado,
    notificar_erro_vault_timeout,
    notificar_erro_download,
    notificar_erro_upload,
    notificar_erro_exclusao_conta,
)
from utils.excecoes import (
    ErroVaultTimeout,
    ErroVaultFalha,
    ErroDownload,
    ErroUpload,
    ErroExclusaoConta,
    ErroEspacoInsuficiente,
)
from utils.logger import obter_logger

logger = obter_logger("orquestrador")

# Fração do espaço livre em PASTA_VAULT que pode ser usada por um backup.
# 80% deixa folga para metadados do filesystem, outros backups concorrentes,
# e o ZIP de saída (que ocupa ~mesmo tamanho dos exports — ZIP_STORED).
_FRACAO_DISCO_UTILIZAVEL = 0.80


def _verificar_capacidade_disco(
    export_email_resultado: dict,
    export_drive_resultado: dict,
    pasta_destino,
) -> None:
    """Pre-flight: aborta o backup antes do download se não couber em disco.

    Lê o `stats.sizeInBytes` reportado pelo Vault para cada export
    (e-mail + Drive) e compara com `disk_usage(pasta_destino).free *
    _FRACAO_DISCO_UTILIZAVEL`. Se não couber, lança
    `ErroEspacoInsuficiente` — preserva horas de I/O em downloads
    que iriam fatalmente falhar e evita resíduos parciais em /mnt/hdd.

    Args:
        export_email_resultado: dict do Vault com chave `stats.sizeInBytes`.
        export_drive_resultado: idem.
        pasta_destino: Path da pasta onde o download vai ser escrito.

    Raises:
        ErroEspacoInsuficiente: necessário > disco_livre * fração.
    """
    tamanho_email = int(export_email_resultado.get("stats", {}).get("sizeInBytes") or 0)
    tamanho_drive = int(export_drive_resultado.get("stats", {}).get("sizeInBytes") or 0)
    necessario = tamanho_email + tamanho_drive

    disco_livre = shutil.disk_usage(pasta_destino).free
    utilizavel = int(disco_livre * _FRACAO_DISCO_UTILIZAVEL)

    necessario_gb = necessario / (1024 ** 3)
    utilizavel_gb = utilizavel / (1024 ** 3)
    disco_livre_gb = disco_livre / (1024 ** 3)

    logger.info(
        f"Pre-flight de capacidade — "
        f"necessario: {necessario_gb:.2f} GB "
        f"(email: {tamanho_email / (1024**3):.2f} GB + drive: {tamanho_drive / (1024**3):.2f} GB), "
        f"disco livre: {disco_livre_gb:.2f} GB, "
        f"utilizavel ({int(_FRACAO_DISCO_UTILIZAVEL * 100)}%): {utilizavel_gb:.2f} GB"
    )

    if necessario == 0:
        # Vault ainda não reportou tamanho — segue e deixa o download decidir.
        logger.warning("Vault não retornou sizeInBytes — pre-flight inconclusivo, prosseguindo")
        return

    if necessario > utilizavel:
        raise ErroEspacoInsuficiente(
            f"Backup nao cabe no disco: necessario {necessario_gb:.2f} GB, "
            f"disponivel {disco_livre_gb:.2f} GB (utilizavel {utilizavel_gb:.2f} GB "
            f"considerando margem de {int((1 - _FRACAO_DISCO_UTILIZAVEL) * 100)}%)",
            necessario_gb=necessario_gb,
            disponivel_gb=disco_livre_gb,
        )


def esta_em_processamento(email: str) -> bool:
    """
    Verifica se já existe um backup em andamento para o e-mail.
    Consulta o banco de dados (funciona com múltiplos workers Celery).

    Args:
        email: E-mail do colaborador

    Returns:
        True se já existe um processamento ativo para esse e-mail
    """
    try:
        from dados.repositorio_backups import existe_backup_em_andamento
        return existe_backup_em_andamento(email)
    except Exception as erro:
        logger.error(f"Erro ao verificar processamento ativo no banco: {erro}")
        return False


def registrar_celery_task_id(email: str, task_id: str) -> None:
    """Salva o ID da task Celery no banco para rastreabilidade."""
    try:
        from dados.repositorio_backups import salvar_celery_task_id
        salvar_celery_task_id(email, task_id)
    except Exception as erro:
        logger.warning(f"Não foi possível salvar celery_task_id: {erro}")


def iniciar_backup_async(email: str, ticket_id: str, nome: str = None, deletar_conta: bool = True) -> None:
    """
    Enfileira o backup no Celery para execução assíncrona.
    Retorna imediatamente — o worker processa em background.

    Args:
        email:         E-mail do colaborador desligado
        ticket_id:     Chave do ticket no Jira
        nome:          Nome do colaborador (opcional)
        deletar_conta: Se True, exclui a conta Google Workspace ao final (padrão: True)
    """
    from worker.tarefas import executar_backup as celery_task
    result = celery_task.delay(email, ticket_id, nome, deletar_conta)
    logger.info(
        f"Backup enfileirado no Celery — Task ID: {result.id}, E-mail: {email}, "
        f"Deletar conta: {deletar_conta}"
    )


def executar_backup_direto(email: str, ticket_id: str, nome: str = None, deletar_conta: bool = True) -> None:
    """
    Executa o fluxo completo de backup de forma SÍNCRONA.
    Chamado diretamente pelo worker Celery.

    Args:
        email:         E-mail do colaborador desligado
        ticket_id:     Chave do ticket no Jira
        nome:          Nome do colaborador (opcional)
        deletar_conta: Se True, exclui a conta Google Workspace ao final (padrão: True)
    """
    identificador = nome or email
    logger.info(f"{'=' * 60}")
    logger.info(f"INÍCIO DO BACKUP — {identificador} ({email})")
    logger.info(f"Ticket: {ticket_id}")
    logger.info(f"{'=' * 60}")

    try:
        registrar_backup(email, ticket_id, nome, deletar_conta=deletar_conta)
    except ValueError as erro:
        # Race condition: dois webhooks chegaram ao mesmo tempo para o mesmo e-mail.
        # O índice único do banco rejeitou a inserção duplicada — encerra sem ação.
        logger.warning(f"Task descartada — {erro}")
        return

    chat_notificar_inicio(email, ticket_id, nome, deletar_conta=deletar_conta)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pasta_colaborador = PASTA_VAULT / f"{email}_{timestamp}"
    pasta_colaborador.mkdir(parents=True, exist_ok=True)

    # ZIP em pasta irmã (não filha) para não dobrar o pico de uso de disco:
    # se ficasse dentro de pasta_colaborador, durante a compactação teríamos
    # os exports brutos + o ZIP final no mesmo volume sem chance de liberar
    # nada até o upload terminar. Pasta irmã permite limpar a pasta dos
    # exports assim que o ZIP fecha.
    nome_zip = f"{email}.zip"
    pasta_zips = PASTA_VAULT / "zips"
    pasta_zips.mkdir(parents=True, exist_ok=True)
    caminho_zip = pasta_zips / f"{email}_{timestamp}.zip"
    link_drive = None
    sha256_zip = None
    _backup_concluido = False
    # Flag usada pelo `finally`: quando o destino e NAS, o ZIP permanece em
    # /mnt/hdd/sync_nas/ para o NAS coletar e NAO deve ser apagado pelo
    # limpar_arquivo_zip da limpeza imediata. A limpeza desse ZIP e feita
    # depois pela limpar_zips_sincronizados (apos NAS_SYNC_RETENCAO_DIAS).
    _manter_zip_local = False

    try:
        # ─────────────────────────────────────────────────────────
        # ETAPA 1: Notificar Jira
        # ─────────────────────────────────────────────────────────
        logger.info("[ETAPA 1/8] Notificando Jira sobre início do backup...")
        atualizar_etapa(email, 1, STATUS_EM_ANDAMENTO)
        comentar_inicio(ticket_id, email, deletar_conta=deletar_conta)
        transicionar_para_status(ticket_id, "Em análise")
        atualizar_etapa(email, 1, STATUS_CONCLUIDO)

        # ─────────────────────────────────────────────────────────
        # ETAPA 2: Criar exportações no Google Vault
        # ─────────────────────────────────────────────────────────
        logger.info("[ETAPA 2/8] Criando exportações no Google Vault...")
        atualizar_etapa(email, 2, STATUS_EM_ANDAMENTO)
        comentar_progresso(ticket_id, "Criando exportações de E-mail e Drive no Google Vault")

        export_email = criar_exportacao_email(email)
        export_email_id = export_email.get("id")
        email_reaproveitado = export_email.get("_reaproveitado", False)

        if email_reaproveitado:
            logger.info(f"Export de E-MAIL reaproveitado: {export_email_id}")
            chat_notificar_vault_reaproveitado(
                email=email, ticket_id=ticket_id,
                email_reaproveitado=True, drive_reaproveitado=False,
                export_email_id=export_email_id, nome=nome,
            )

        try:
            export_drive = criar_exportacao_drive(email)
        except Exception:
            # Export de e-mail foi criado com sucesso, mas o de Drive falhou.
            # monitorar_exportacao nunca será chamada para o e-mail, portanto o
            # slot do semáforo ficaria retido permanentemente — liberamos aqui.
            liberar_semaforo_exportacao(export_email)
            raise

        export_drive_id = export_drive.get("id")
        drive_reaproveitado = export_drive.get("_reaproveitado", False)

        if drive_reaproveitado:
            logger.info(f"Export de DRIVE reaproveitado: {export_drive_id}")
            chat_notificar_vault_reaproveitado(
                email=email, ticket_id=ticket_id,
                email_reaproveitado=False, drive_reaproveitado=True,
                export_drive_id=export_drive_id, nome=nome,
            )

        atualizar_etapa(email, 2, STATUS_CONCLUIDO)

        # ─────────────────────────────────────────────────────────
        # ETAPA 3: Monitorar exportações em paralelo
        # ─────────────────────────────────────────────────────────
        logger.info("[ETAPA 3/8] Monitorando exportações (aguardando conclusão)...")
        atualizar_etapa(email, 3, STATUS_EM_ANDAMENTO)
        comentar_progresso(
            ticket_id,
            "Exportações criadas. Aguardando conclusão (pode levar algumas horas)..."
        )

        export_email_resultado = None
        export_drive_resultado = None

        with ThreadPoolExecutor(max_workers=2) as executor:
            futuro_email = executor.submit(
                monitorar_exportacao, export_email_id, not email_reaproveitado
            )
            futuro_drive = executor.submit(
                monitorar_exportacao, export_drive_id, not drive_reaproveitado
            )

            for futuro in as_completed([futuro_email, futuro_drive]):
                try:
                    resultado = futuro.result()
                    if futuro == futuro_email:
                        export_email_resultado = resultado
                        logger.info(f"Exportação de E-MAIL concluída: {resultado.get('name')}")
                    else:
                        export_drive_resultado = resultado
                        logger.info(f"Exportação de DRIVE concluída: {resultado.get('name')}")
                except Exception as erro_export:
                    # Re-raise direto para preservar atributos (ex: stats em ErroVaultTimeout)
                    if isinstance(erro_export, (ErroVaultTimeout, ErroVaultFalha)):
                        raise erro_export
                    msg = str(erro_export)
                    if "Timeout" in msg:
                        raise ErroVaultTimeout(msg)
                    elif "FALHOU" in msg:
                        raise ErroVaultFalha(msg)
                    raise

        atualizar_etapa(email, 3, STATUS_CONCLUIDO)

        # ─────────────────────────────────────────────────────────
        # Pre-flight: backup cabe no disco?
        # ─────────────────────────────────────────────────────────
        # Validação executada DEPOIS do export concluir (porque só aqui
        # o Vault tem o sizeInBytes definitivo) e ANTES de iniciar o
        # download. Aborta cedo se inviável, evitando horas de I/O em
        # download que vai falhar e resíduos parciais em /mnt/hdd.
        _verificar_capacidade_disco(
            export_email_resultado, export_drive_resultado, PASTA_VAULT,
        )

        # ─────────────────────────────────────────────────────────
        # ETAPA 4: Baixar arquivos exportados
        # ─────────────────────────────────────────────────────────
        logger.info("[ETAPA 4/8] Baixando arquivos exportados...")
        atualizar_etapa(email, 4, STATUS_EM_ANDAMENTO)
        comentar_progresso(ticket_id, "Exportações concluídas. Baixando arquivos...")

        pasta_email = pasta_colaborador / "email"
        pasta_drive = pasta_colaborador / "drive"

        try:
            total_email = len(export_email_resultado.get("cloudStorageSink", {}).get("files", []))
            total_drive = len(export_drive_resultado.get("cloudStorageSink", {}).get("files", []))
            total_arquivos_dl = max(total_email + total_drive, 1)

            # Fatores de peso de cada export no progresso global (0–100%)
            fator_email = total_email / total_arquivos_dl
            fator_drive = total_drive / total_arquivos_dl

            # Progresso individual de cada export — atualizado pelas threads
            _pct = {"email": 0, "drive": 0}
            _lock_pct = threading.Lock()

            def _on_progresso_email(pct):
                with _lock_pct:
                    _pct["email"] = pct
                    global_pct = int(_pct["email"] * fator_email + _pct["drive"] * fator_drive)
                atualizar_progresso(email, 4, global_pct)

            def _on_progresso_drive(pct):
                with _lock_pct:
                    _pct["drive"] = pct
                    global_pct = int(_pct["email"] * fator_email + _pct["drive"] * fator_drive)
                atualizar_progresso(email, 4, global_pct)

            # Downloads de e-mail e Drive em paralelo
            with ThreadPoolExecutor(max_workers=2) as dl_executor:
                futuro_email_dl = dl_executor.submit(
                    baixar_exportacao, export_email_resultado, pasta_email, _on_progresso_email
                )
                futuro_drive_dl = dl_executor.submit(
                    baixar_exportacao, export_drive_resultado, pasta_drive, _on_progresso_drive
                )
                arquivos_email = futuro_email_dl.result()
                arquivos_drive = futuro_drive_dl.result()
        except Exception as erro:
            raise ErroDownload(str(erro)) from erro

        logger.info(f"Total de arquivos baixados: {len(arquivos_email) + len(arquivos_drive)}")
        atualizar_etapa(email, 4, STATUS_CONCLUIDO)

        # ─────────────────────────────────────────────────────────
        # ETAPA 5: Compactar em .zip + SHA256
        # ─────────────────────────────────────────────────────────
        logger.info("[ETAPA 5/8] Compactando arquivos em .zip...")
        atualizar_etapa(email, 5, STATUS_EM_ANDAMENTO)
        comentar_progresso(ticket_id, "Compactando arquivos em ZIP...")

        caminho_zip, sha256_zip = compactar_arquivos(
            pasta_colaborador,
            caminho_zip,
            on_progresso=lambda pct: atualizar_progresso(email, 5, pct),
        )
        logger.info(f"Integridade ZIP — SHA256: {sha256_zip}")
        atualizar_etapa(email, 5, STATUS_CONCLUIDO)

        # ─────────────────────────────────────────────────────────
        # ETAPA 6: Disponibilizar .zip para o NAS Synology (com fallback Drive)
        # ─────────────────────────────────────────────────────────
        # Estrategia: o NAS faz pull do disco local (montado nele via SMB/NFS).
        # O servidor apenas MOVE o ZIP para NAS_SYNC_DIR e cria um marker .ready.
        # Se isso falhar (disco cheio, permissao), cai para upload direto no Drive.
        logger.info("[ETAPA 6/8] Disponibilizando .zip para o NAS Synology...")
        atualizar_etapa(email, 6, STATUS_EM_ANDAMENTO)
        comentar_progresso(ticket_id, "Disponibilizando backup para o NAS Synology...")

        destino_usado = "nas"
        tamanho_mb = caminho_zip.stat().st_size / (1024 * 1024)
        try:
            resultado_upload = disponibilizar_para_nas(
                caminho_zip, nome_zip, sha256=sha256_zip,
                on_progresso=lambda pct: atualizar_progresso(email, 6, pct),
            )
        except (ErroNasSync, OSError) as erro_nas:
            logger.warning(
                f"NAS sync falhou ({erro_nas}) — caindo para fallback Google Drive."
            )
            comentar_progresso(
                ticket_id,
                "Falha ao disponibilizar para o NAS — enviando para Google Drive (fallback)..."
            )
            destino_usado = "drive"
            try:
                resultado_upload = fazer_upload(
                    caminho_zip, nome_zip, sha256=sha256_zip,
                    on_progresso=lambda pct: atualizar_progresso(email, 6, pct),
                )
            except Exception as erro:
                raise ErroUpload(str(erro)) from erro

        link_drive = resultado_upload.get("webViewLink", "Link não disponível")
        atualizar_etapa(email, 6, STATUS_CONCLUIDO)

        # ─────────────────────────────────────────────────────────
        # Libera os originais (PSTs do Vault, exports do Drive) IMEDIATAMENTE.
        # O ZIP ja esta seguro (em sync_nas ou no Drive), entao manter os
        # originais aqui so duplica armazenamento e atrasa a liberacao de disco
        # para o proximo backup da fila. Em backups grandes (470 GB+ de PSTs)
        # isso reduz o peak de disco pela metade durante o restante do ciclo.
        # A chamada e segura: idempotente (no-op se a pasta ja foi apagada) e
        # o `finally` no fim do try refaz como salvaguarda.
        # ─────────────────────────────────────────────────────────
        logger.info("Removendo originais baixados — ZIP ja consolidado.")
        limpar_arquivos_temporarios(pasta_colaborador)

        # ─────────────────────────────────────────────────────────
        # Fluxo bifurca apos a Etapa 6: NAS (aguarda 23h e monitor finaliza)
        # vs Drive (fallback — fluxo classico Etapas 7 e 8).
        # ─────────────────────────────────────────────────────────
        if destino_usado == "nas":
            # Worker terminou. O ZIP esta em /mnt/hdd/sync_nas/ aguardando o
            # NAS Synology coletar (janela de 23h, responsabilidade externa).
            # Apos isso, o monitor `processamento.finalizacao_nas` fechara o
            # ticket no Jira, excluira a conta Workspace (se aplicavel) e
            # promovera o marker .ready -> .uploaded para a limpeza apagar
            # o ZIP local depois de NAS_SYNC_RETENCAO_DIAS dias.
            from dados.repositorio_backups import marcar_aguardando_nas
            marcar_aguardando_nas(email, link_drive)
            comentar_progresso(
                ticket_id,
                "Backup compactado e disponibilizado para coleta pelo NAS Synology. "
                "Encerramento automatico do chamado em ate 23 horas."
            )
            # Etapas 7 e 8 ficam como concluidas no rastreador (visivel no dashboard).
            # O ciclo final (Jira + exclusao + chat) e atribuido ao monitor.
            atualizar_etapa(email, 7, STATUS_CONCLUIDO)
            atualizar_etapa(email, 8, STATUS_CONCLUIDO)

            logger.info(f"{'=' * 60}")
            logger.info(f"BACKUP AGUARDANDO NAS — {identificador}")
            logger.info(f"Caminho local: {link_drive}")
            logger.info(f"SHA256 ZIP: {sha256_zip}")
            logger.info(f"{'=' * 60}")

            # Limpa apenas a pasta_colaborador (exports brutos do Vault).
            # O ZIP em sync_nas DEVE permanecer ate o monitor promover o marker.
            _backup_concluido = True
            _manter_zip_local = True
        else:
            # destino_usado == "drive" (fallback) — fluxo original abaixo
            # ─────────────────────────────────────────────────────────
            # ETAPA 7: Atualizar Jira
            # ─────────────────────────────────────────────────────────
            logger.info("[ETAPA 7/8] Atualizando ticket Jira com resultado...")
            atualizar_etapa(email, 7, STATUS_EM_ANDAMENTO)
            comentar_sucesso(ticket_id, email, link_drive, deletar_conta=deletar_conta)
            atualizar_etapa(email, 7, STATUS_CONCLUIDO)

            # ─────────────────────────────────────────────────────────
            # ETAPA 8: Verificar backup + excluir conta (se habilitado)
            # ─────────────────────────────────────────────────────────
            if deletar_conta:
                logger.info("[ETAPA 8/8] Verificando backup no Drive e excluindo conta...")
            else:
                logger.info("[ETAPA 8/8] Verificando backup no Drive (exclusão de conta desativada)...")
            atualizar_etapa(email, 8, STATUS_EM_ANDAMENTO)

            arquivo_id = resultado_upload.get("id")

            if deletar_conta:
                comentar_progresso(ticket_id, "Verificando backup no Drive Compartilhado antes de excluir a conta...")
                try:
                    resultado_exclusao = verificar_e_deletar_conta(email, arquivo_id)
                except Exception as erro:
                    raise ErroExclusaoConta(str(erro)) from erro
                logger.info(f"Conta excluída: {resultado_exclusao}")
                comentar_conta_excluida(ticket_id, email)
                chat_notificar_conta_excluida(email, ticket_id, nome)
            else:
                logger.info(f"Exclusão de conta desativada para este backup — conta de {email} mantida")

            submeter_formularios_pendentes(ticket_id)

            transicionar_resolvido(ticket_id)

            atualizar_etapa(email, 8, STATUS_CONCLUIDO)

            _backup_concluido = True
            finalizar_backup(email, sucesso=True, link_drive=link_drive, sha256_zip=sha256_zip)
            chat_notificar_sucesso(email, ticket_id, link_drive, nome, deletar_conta=deletar_conta)

            logger.info(f"{'=' * 60}")
            if deletar_conta:
                logger.info(f"BACKUP CONCLUÍDO E CONTA EXCLUÍDA — {identificador}")
            else:
                logger.info(f"BACKUP CONCLUÍDO (conta mantida) — {identificador}")
            logger.info(f"Destino: DRIVE | Link: {link_drive}")
            logger.info(f"SHA256 ZIP: {sha256_zip}")
            logger.info(f"{'=' * 60}")

    except ErroVaultTimeout as erro:
        _tratar_erro(email, ticket_id, nome, str(erro))
        export_id = export_email_id if "E-mail" in str(erro) else export_drive_id
        horas = float(str(erro).split("horas")[0].split()[-1]) if "horas" in str(erro) else 24.0
        stats = getattr(erro, "stats", {})
        notificar_erro_vault_timeout(
            email, ticket_id, export_id or "?", horas, nome,
            artefatos_exportados=int(stats.get("exportedArtifactCount") or 0),
            artefatos_total=int(stats.get("totalArtifactCount") or 0),
            tamanho_mb=int(stats.get("sizeInBytes") or 0) / (1024 * 1024),
        )

    except ErroEspacoInsuficiente as erro:
        # Aborta o backup com mensagem clara — não vale a pena tentar
        # de novo até alguém expandir o disco ou reduzir o escopo.
        _tratar_erro(email, ticket_id, nome, str(erro))
        chat_notificar_erro(email, ticket_id, str(erro), nome)

    except ErroDownload as erro:
        _tratar_erro(email, ticket_id, nome, str(erro))
        notificar_erro_download(email, ticket_id, str(erro)[:80], 3, nome)

    except ErroUpload as erro:
        _tratar_erro(email, ticket_id, nome, str(erro))
        notificar_erro_upload(email, ticket_id, tamanho_mb if "tamanho_mb" in dir() else 0, 3, nome)

    except ErroExclusaoConta as erro:
        # Backup concluído, mas conta não foi excluída — não é erro total
        logger.error(f"ERRO na exclusão da conta de {email}: {erro}")
        _backup_concluido = True
        finalizar_backup(email, sucesso=True, link_drive=link_drive, sha256_zip=sha256_zip)
        notificar_erro_exclusao_conta(email, ticket_id, str(erro), nome)

    except Exception as erro:
        _tratar_erro(email, ticket_id, nome, str(erro))
        chat_notificar_erro(email, ticket_id, str(erro), nome)

    finally:
        if _backup_concluido:
            logger.info("Executando limpeza de arquivos temporários...")
            limpar_arquivos_temporarios(pasta_colaborador)
            if not _manter_zip_local:
                limpar_arquivo_zip(caminho_zip)
            else:
                logger.info(
                    "ZIP preservado em sync_nas — sera apagado por "
                    "limpar_zips_sincronizados apos NAS_SYNC_RETENCAO_DIAS."
                )
        else:
            logger.info("Backup não concluído — arquivos preservados para reprocessamento.")


def _tratar_erro(email: str, ticket_id: str, nome: str, mensagem: str) -> None:
    """Registra o erro no rastreador. Erros vão apenas para o Google Chat, não para o Jira."""
    logger.error(f"ERRO no backup de {email}: {mensagem}", exc_info=True)
    finalizar_backup(email, sucesso=False, erro_mensagem=mensagem)
    logger.error(f"{'=' * 60}")
    logger.error(f"BACKUP FALHOU — {nome or email}")
    logger.error(f"{'=' * 60}")
