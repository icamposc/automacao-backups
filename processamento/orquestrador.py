"""
============================================================
Módulo Orquestrador — Automação de Backups
============================================================
Versão: 1.1.0
Data: 2026-03-10
Descrição: Cérebro do sistema. Coordena todo o fluxo de backup
           de um colaborador desligado:
           1. Notifica o Jira que o backup iniciou
           2. Cria exportações de Email e Drive no Vault
           3. Monitora ambas em paralelo
           4. Baixa arquivos exportados
           5. Compacta tudo em .zip
           6. Faz upload para o Google Drive Compartilhado
           7. Atualiza o ticket Jira com o resultado
           8. Verifica backup no Drive e exclui conta do colaborador
           9. Limpa arquivos temporários

           Este módulo é executado em uma thread de background
           para não bloquear a resposta HTTP do webhook.
============================================================
Histórico:
  1.1.0 (2026-03-10) — Adicionada Etapa 8: exclusão da conta
                        do colaborador após verificação do backup
  1.0.0 (2026-02-19) — Versão inicial
============================================================
"""

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime

from servicos.vault_exportacao import (
    criar_exportacao_email,
    criar_exportacao_drive,
    monitorar_exportacao,
    baixar_exportacao,
)
from servicos.drive_upload import fazer_upload
from servicos.conta_exclusao import verificar_e_deletar_conta
from servicos.jira_atualizacao import (
    comentar_inicio,
    comentar_progresso,
    comentar_sucesso,
    comentar_erro,
    comentar_conta_excluida,
)
from processamento.compactacao import compactar_arquivos
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
)
from config.configuracoes import PASTA_TEMP
from utils.logger import obter_logger

logger = obter_logger("orquestrador")

# Controle de processamentos ativos — evita duplicatas
# Chave: e-mail do colaborador, Valor: True/False (em processamento)
_processamentos_ativos = {}
_lock_processamentos = threading.Lock()


def esta_em_processamento(email: str) -> bool:
    """
    Verifica se já existe um backup em andamento para o e-mail informado.

    Usado para evitar processamentos duplicados caso o webhook
    seja enviado mais de uma vez para o mesmo colaborador.

    Args:
        email: E-mail do colaborador

    Returns:
        True se já existe um processamento ativo para esse e-mail
    """
    with _lock_processamentos:
        return _processamentos_ativos.get(email, False)


def _marcar_inicio(email: str) -> None:
    """Marca o início do processamento para um e-mail."""
    with _lock_processamentos:
        _processamentos_ativos[email] = True
    logger.info(f"Processamento marcado como ATIVO para: {email}")


def _marcar_fim(email: str) -> None:
    """Marca o fim do processamento para um e-mail."""
    with _lock_processamentos:
        _processamentos_ativos[email] = False
    logger.info(f"Processamento marcado como FINALIZADO para: {email}")


def iniciar_backup_async(email: str, ticket_id: str, nome: str = None) -> None:
    """
    Inicia o processo de backup em uma thread de background.

    Esta função retorna imediatamente, permitindo que o servidor
    Flask responda HTTP 200 ao webhook sem esperar o backup terminar.

    Args:
        email: E-mail do colaborador desligado
        ticket_id: Chave do ticket no Jira (ex: "SPN-123")
        nome: Nome do colaborador (opcional, usado nos logs e comentários)
    """
    logger.info(f"Iniciando thread de backup para: {email} (Ticket: {ticket_id})")

    thread = threading.Thread(
        target=_executar_backup,
        args=(email, ticket_id, nome),
        name=f"backup-{email}",
        daemon=True,  # A thread é encerrada se o processo principal morrer
    )
    thread.start()

    logger.info(f"Thread de backup iniciada: {thread.name}")


def _executar_backup(email: str, ticket_id: str, nome: str = None) -> None:
    """
    Executa o fluxo completo de backup de um colaborador desligado.

    Esta é a função principal que coordena todas as etapas.
    Roda em uma thread de background.

    Args:
        email: E-mail do colaborador desligado
        ticket_id: Chave do ticket no Jira
        nome: Nome do colaborador (opcional)
    """
    identificador = nome or email
    logger.info(f"{'=' * 60}")
    logger.info(f"INÍCIO DO BACKUP — {identificador} ({email})")
    logger.info(f"Ticket: {ticket_id}")
    logger.info(f"{'=' * 60}")

    _marcar_inicio(email)

    # Registra o backup no rastreador (Dashboard + Google Chat)
    registrar_backup(email, ticket_id, nome)
    chat_notificar_inicio(email, ticket_id, nome)

    # Pasta temporária exclusiva para este colaborador
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pasta_colaborador = PASTA_TEMP / f"{email}_{timestamp}"
    pasta_colaborador.mkdir(parents=True, exist_ok=True)

    # Caminho do arquivo ZIP final
    nome_zip = f"{email}.zip"
    caminho_zip = PASTA_TEMP / nome_zip

    # Variável para armazenar o link do Drive em caso de sucesso
    link_drive = None

    try:
        # ============================================================
        # ETAPA 1: Notificar Jira que o backup iniciou
        # ============================================================
        logger.info("[ETAPA 1/8] Notificando Jira sobre início do backup...")
        atualizar_etapa(email, 1, STATUS_EM_ANDAMENTO)
        comentar_inicio(ticket_id, email)
        atualizar_etapa(email, 1, STATUS_CONCLUIDO)

        # ============================================================
        # ETAPA 2: Criar exportações no Google Vault
        # ============================================================
        logger.info("[ETAPA 2/8] Criando exportações no Google Vault...")
        atualizar_etapa(email, 2, STATUS_EM_ANDAMENTO)
        comentar_progresso(ticket_id, "Criando exportações de E-mail e Drive no Google Vault")

        # Cria ambas as exportações (Email e Drive)
        export_email = criar_exportacao_email(email)
        export_drive = criar_exportacao_drive(email)

        export_email_id = export_email.get("id")
        export_drive_id = export_drive.get("id")

        logger.info(f"Exportações criadas — Email ID: {export_email_id}, Drive ID: {export_drive_id}")
        atualizar_etapa(email, 2, STATUS_CONCLUIDO)

        # ============================================================
        # ETAPA 3: Monitorar exportações em paralelo
        # ============================================================
        logger.info("[ETAPA 3/8] Monitorando exportações (aguardando conclusão)...")
        atualizar_etapa(email, 3, STATUS_EM_ANDAMENTO)
        comentar_progresso(
            ticket_id,
            "Exportações criadas. Aguardando conclusão (pode levar algumas horas)..."
        )

        # Monitora ambas as exportações em paralelo usando threads
        export_email_resultado = None
        export_drive_resultado = None

        with ThreadPoolExecutor(max_workers=2) as executor:
            # Submete o monitoramento das duas exportações
            futuro_email = executor.submit(monitorar_exportacao, export_email_id)
            futuro_drive = executor.submit(monitorar_exportacao, export_drive_id)

            # Aguarda ambas terminarem
            for futuro in as_completed([futuro_email, futuro_drive]):
                try:
                    resultado = futuro.result()
                    nome_export = resultado.get("name", "")

                    if futuro == futuro_email:
                        export_email_resultado = resultado
                        logger.info(f"Exportação de E-MAIL concluída: {nome_export}")
                    else:
                        export_drive_resultado = resultado
                        logger.info(f"Exportação de DRIVE concluída: {nome_export}")

                except Exception as erro_export:
                    # Se uma exportação falhar, tenta recriar uma vez
                    logger.error(f"Exportação falhou: {erro_export}")
                    raise

        atualizar_etapa(email, 3, STATUS_CONCLUIDO)

        # ============================================================
        # ETAPA 4: Baixar arquivos exportados
        # ============================================================
        logger.info("[ETAPA 4/8] Baixando arquivos exportados...")
        atualizar_etapa(email, 4, STATUS_EM_ANDAMENTO)
        comentar_progresso(ticket_id, "Exportações concluídas. Baixando arquivos...")

        # Cria subpastas para cada tipo de exportação
        pasta_email = pasta_colaborador / "email"
        pasta_drive = pasta_colaborador / "drive"

        arquivos_email = baixar_exportacao(export_email_resultado, pasta_email)
        arquivos_drive = baixar_exportacao(export_drive_resultado, pasta_drive)

        total_arquivos = len(arquivos_email) + len(arquivos_drive)
        logger.info(f"Total de arquivos baixados: {total_arquivos}")
        atualizar_etapa(email, 4, STATUS_CONCLUIDO)

        # ============================================================
        # ETAPA 5: Compactar em .zip
        # ============================================================
        logger.info("[ETAPA 5/8] Compactando arquivos em .zip...")
        atualizar_etapa(email, 5, STATUS_EM_ANDAMENTO)
        comentar_progresso(ticket_id, "Compactando arquivos em ZIP...")

        caminho_zip = compactar_arquivos(pasta_colaborador, caminho_zip)
        atualizar_etapa(email, 5, STATUS_CONCLUIDO)

        # ============================================================
        # ETAPA 6: Upload para Google Drive Compartilhado
        # ============================================================
        logger.info("[ETAPA 6/8] Enviando .zip para Google Drive Compartilhado...")
        atualizar_etapa(email, 6, STATUS_EM_ANDAMENTO)
        comentar_progresso(ticket_id, "Enviando backup para Google Drive Compartilhado...")

        resultado_upload = fazer_upload(caminho_zip, nome_zip)
        link_drive = resultado_upload.get("webViewLink", "Link não disponível")
        atualizar_etapa(email, 6, STATUS_CONCLUIDO)

        # ============================================================
        # ETAPA 7: Atualizar Jira com resultado final
        # ============================================================
        logger.info("[ETAPA 7/8] Atualizando ticket Jira com resultado...")
        atualizar_etapa(email, 7, STATUS_EM_ANDAMENTO)
        comentar_sucesso(ticket_id, email, link_drive)
        atualizar_etapa(email, 7, STATUS_CONCLUIDO)

        # ============================================================
        # ETAPA 8: Verificar backup no Drive e excluir conta
        # ============================================================
        logger.info("[ETAPA 8/8] Verificando backup no Drive e excluindo conta...")
        atualizar_etapa(email, 8, STATUS_EM_ANDAMENTO)
        comentar_progresso(ticket_id, "Verificando backup no Drive Compartilhado antes de excluir a conta...")

        arquivo_id = resultado_upload.get("id")

        resultado_exclusao = verificar_e_deletar_conta(email, arquivo_id)

        logger.info(f"Conta excluída: {resultado_exclusao}")
        comentar_conta_excluida(ticket_id, email)
        chat_notificar_conta_excluida(email, ticket_id, nome)
        atualizar_etapa(email, 8, STATUS_CONCLUIDO)

        # Finaliza o backup no rastreador com sucesso
        finalizar_backup(email, sucesso=True, link_drive=link_drive)
        chat_notificar_sucesso(email, ticket_id, link_drive, nome)

        logger.info(f"{'=' * 60}")
        logger.info(f"BACKUP CONCLUÍDO E CONTA EXCLUÍDA — {identificador}")
        logger.info(f"Link no Drive: {link_drive}")
        logger.info(f"{'=' * 60}")

    except Exception as erro:
        # ============================================================
        # TRATAMENTO DE ERRO GERAL
        # ============================================================
        logger.error(f"ERRO no backup de {email}: {erro}", exc_info=True)

        # Notifica o Jira e o Google Chat sobre o erro
        comentar_erro(ticket_id, email, str(erro))
        chat_notificar_erro(email, ticket_id, str(erro), nome)

        # Finaliza o backup no rastreador com erro
        finalizar_backup(email, sucesso=False, erro_mensagem=str(erro), link_drive=link_drive)

        logger.error(f"{'=' * 60}")
        logger.error(f"BACKUP FALHOU — {identificador}")
        logger.error(f"{'=' * 60}")

    finally:
        # ============================================================
        # LIMPEZA: Sempre remove arquivos temporários
        # ============================================================
        logger.info("Executando limpeza de arquivos temporários...")
        limpar_arquivos_temporarios(pasta_colaborador)
        limpar_arquivo_zip(caminho_zip)

        # Marca o processamento como finalizado
        _marcar_fim(email)
