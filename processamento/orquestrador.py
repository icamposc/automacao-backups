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
from servicos.conta_exclusao import verificar_e_deletar_conta
from servicos.jira_atualizacao import (
    comentar_inicio,
    comentar_progresso,
    comentar_sucesso,
    comentar_erro,
    comentar_conta_excluida,
    transicionar_ticket,
    transicionar_resolvido,
    submeter_formularios_pendentes,
)
from config.configuracoes import JIRA_TRANSICAO_EM_ANALISE, JIRA_TRANSICAO_RESOLVIDO, PASTA_VAULT
from processamento.compactacao import compactar_arquivos, calcular_sha256
from processamento.limpeza import limpar_arquivos_temporarios, limpar_arquivo_zip
from processamento.rastreador import (
    registrar_backup,
    atualizar_etapa,
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
)
from utils.logger import obter_logger

logger = obter_logger("orquestrador")


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


def iniciar_backup_async(email: str, ticket_id: str, nome: str = None) -> None:
    """
    Enfileira o backup no Celery para execução assíncrona.
    Retorna imediatamente — o worker processa em background.

    Args:
        email:     E-mail do colaborador desligado
        ticket_id: Chave do ticket no Jira
        nome:      Nome do colaborador (opcional)
    """
    from worker.tarefas import executar_backup as celery_task
    result = celery_task.delay(email, ticket_id, nome)
    logger.info(f"Backup enfileirado no Celery — Task ID: {result.id}, E-mail: {email}")


def executar_backup_direto(email: str, ticket_id: str, nome: str = None) -> None:
    """
    Executa o fluxo completo de backup de forma SÍNCRONA.
    Chamado diretamente pelo worker Celery.

    Args:
        email:     E-mail do colaborador desligado
        ticket_id: Chave do ticket no Jira
        nome:      Nome do colaborador (opcional)
    """
    identificador = nome or email
    logger.info(f"{'=' * 60}")
    logger.info(f"INÍCIO DO BACKUP — {identificador} ({email})")
    logger.info(f"Ticket: {ticket_id}")
    logger.info(f"{'=' * 60}")

    try:
        registrar_backup(email, ticket_id, nome)
    except ValueError as erro:
        # Race condition: dois webhooks chegaram ao mesmo tempo para o mesmo e-mail.
        # O índice único do banco rejeitou a inserção duplicada — encerra sem ação.
        logger.warning(f"Task descartada — {erro}")
        return

    chat_notificar_inicio(email, ticket_id, nome)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pasta_colaborador = PASTA_VAULT / f"{email}_{timestamp}"
    pasta_colaborador.mkdir(parents=True, exist_ok=True)

    nome_zip = f"{email}.zip"
    caminho_zip = PASTA_VAULT / nome_zip
    link_drive = None
    sha256_zip = None

    try:
        # ─────────────────────────────────────────────────────────
        # ETAPA 1: Notificar Jira
        # ─────────────────────────────────────────────────────────
        logger.info("[ETAPA 1/8] Notificando Jira sobre início do backup...")
        atualizar_etapa(email, 1, STATUS_EM_ANDAMENTO)
        comentar_inicio(ticket_id, email)
        if JIRA_TRANSICAO_EM_ANALISE:
            transicionar_ticket(ticket_id, JIRA_TRANSICAO_EM_ANALISE)
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
        # ETAPA 4: Baixar arquivos exportados
        # ─────────────────────────────────────────────────────────
        logger.info("[ETAPA 4/8] Baixando arquivos exportados...")
        atualizar_etapa(email, 4, STATUS_EM_ANDAMENTO)
        comentar_progresso(ticket_id, "Exportações concluídas. Baixando arquivos...")

        pasta_email = pasta_colaborador / "email"
        pasta_drive = pasta_colaborador / "drive"

        try:
            arquivos_email = baixar_exportacao(export_email_resultado, pasta_email)
            arquivos_drive = baixar_exportacao(export_drive_resultado, pasta_drive)
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

        caminho_zip = compactar_arquivos(pasta_colaborador, caminho_zip)
        sha256_zip = calcular_sha256(caminho_zip)
        logger.info(f"Integridade ZIP — SHA256: {sha256_zip}")
        atualizar_etapa(email, 5, STATUS_CONCLUIDO)

        # ─────────────────────────────────────────────────────────
        # ETAPA 6: Upload para Google Drive Compartilhado
        # ─────────────────────────────────────────────────────────
        logger.info("[ETAPA 6/8] Enviando .zip para Google Drive Compartilhado...")
        atualizar_etapa(email, 6, STATUS_EM_ANDAMENTO)
        comentar_progresso(ticket_id, "Enviando backup para Google Drive Compartilhado...")

        try:
            tamanho_mb = caminho_zip.stat().st_size / (1024 * 1024)
            resultado_upload = fazer_upload(caminho_zip, nome_zip, sha256=sha256_zip)
        except Exception as erro:
            raise ErroUpload(str(erro)) from erro

        link_drive = resultado_upload.get("webViewLink", "Link não disponível")
        atualizar_etapa(email, 6, STATUS_CONCLUIDO)

        # ─────────────────────────────────────────────────────────
        # ETAPA 7: Atualizar Jira
        # ─────────────────────────────────────────────────────────
        logger.info("[ETAPA 7/8] Atualizando ticket Jira com resultado...")
        atualizar_etapa(email, 7, STATUS_EM_ANDAMENTO)
        comentar_sucesso(ticket_id, email, link_drive)
        atualizar_etapa(email, 7, STATUS_CONCLUIDO)

        # ─────────────────────────────────────────────────────────
        # ETAPA 8: Verificar backup e excluir conta
        # ─────────────────────────────────────────────────────────
        logger.info("[ETAPA 8/8] Verificando backup no Drive e excluindo conta...")
        atualizar_etapa(email, 8, STATUS_EM_ANDAMENTO)
        comentar_progresso(ticket_id, "Verificando backup no Drive Compartilhado antes de excluir a conta...")

        arquivo_id = resultado_upload.get("id")

        try:
            resultado_exclusao = verificar_e_deletar_conta(email, arquivo_id)
        except Exception as erro:
            raise ErroExclusaoConta(str(erro)) from erro

        logger.info(f"Conta excluída: {resultado_exclusao}")
        comentar_conta_excluida(ticket_id, email)
        submeter_formularios_pendentes(ticket_id)

        if JIRA_TRANSICAO_RESOLVIDO:
            transicionar_resolvido(ticket_id, JIRA_TRANSICAO_RESOLVIDO)

        chat_notificar_conta_excluida(email, ticket_id, nome)
        atualizar_etapa(email, 8, STATUS_CONCLUIDO)

        finalizar_backup(email, sucesso=True, link_drive=link_drive, sha256_zip=sha256_zip)
        chat_notificar_sucesso(email, ticket_id, link_drive, nome)

        logger.info(f"{'=' * 60}")
        logger.info(f"BACKUP CONCLUÍDO E CONTA EXCLUÍDA — {identificador}")
        logger.info(f"Link no Drive: {link_drive}")
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

    except ErroDownload as erro:
        _tratar_erro(email, ticket_id, nome, str(erro))
        notificar_erro_download(email, ticket_id, str(erro)[:80], 3, nome)

    except ErroUpload as erro:
        _tratar_erro(email, ticket_id, nome, str(erro))
        notificar_erro_upload(email, ticket_id, tamanho_mb if "tamanho_mb" in dir() else 0, 3, nome)

    except ErroExclusaoConta as erro:
        # Backup concluído, mas conta não foi excluída — não é erro total
        logger.error(f"ERRO na exclusão da conta de {email}: {erro}")
        comentar_erro(ticket_id, email, str(erro))
        finalizar_backup(email, sucesso=True, link_drive=link_drive, sha256_zip=sha256_zip)
        notificar_erro_exclusao_conta(email, ticket_id, str(erro), nome)

    except Exception as erro:
        _tratar_erro(email, ticket_id, nome, str(erro))
        chat_notificar_erro(email, ticket_id, str(erro), nome)

    finally:
        logger.info("Executando limpeza de arquivos temporários...")
        limpar_arquivos_temporarios(pasta_colaborador)
        limpar_arquivo_zip(caminho_zip)


def _tratar_erro(email: str, ticket_id: str, nome: str, mensagem: str) -> None:
    """Registra o erro no Jira e no rastreador."""
    logger.error(f"ERRO no backup de {email}: {mensagem}", exc_info=True)
    comentar_erro(ticket_id, email, mensagem)
    finalizar_backup(email, sucesso=False, erro_mensagem=mensagem)
    logger.error(f"{'=' * 60}")
    logger.error(f"BACKUP FALHOU — {nome or email}")
    logger.error(f"{'=' * 60}")
