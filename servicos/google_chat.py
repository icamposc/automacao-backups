"""
============================================================
Módulo Google Chat — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-03-10
Descrição: Envia notificações e alertas para o Google Chat
           via Incoming Webhook. Informa a equipe sobre o
           progresso de cada backup em tempo real.

           Falhas no envio NUNCA devem impedir o fluxo
           principal de backup — são apenas registradas nos logs.

Configuração:
  Para usar, crie um webhook no Google Chat:
  1. Abra o espaço/sala desejado no Google Chat
  2. Clique no nome do espaço → "Apps e integrações"
  3. Clique em "Adicionar webhooks"
  4. Dê um nome (ex: "Automação Backups") e copie a URL
  5. Cole a URL na variável GOOGLE_CHAT_WEBHOOK_URL do .env
============================================================
Histórico:
  1.0.0 (2026-03-10) — Versão inicial
============================================================
"""

import requests

from config.configuracoes import GOOGLE_CHAT_WEBHOOK_URL
from utils.logger import obter_logger

logger = obter_logger("google_chat")

# Timeout para requisições ao Google Chat (segundos)
_TIMEOUT = 15


def _enviar_mensagem(texto: str) -> bool:
    """
    Envia uma mensagem de texto para o Google Chat via webhook.

    Args:
        texto: Mensagem a ser enviada (suporta formatação simples do Chat)

    Returns:
        True se a mensagem foi enviada com sucesso, False caso contrário
    """
    if not GOOGLE_CHAT_WEBHOOK_URL:
        logger.debug("Webhook do Google Chat não configurado — notificação ignorada")
        return False

    corpo = {"text": texto}

    try:
        resposta = requests.post(
            GOOGLE_CHAT_WEBHOOK_URL,
            json=corpo,
            timeout=_TIMEOUT,
        )
        resposta.raise_for_status()
        logger.debug("Mensagem enviada ao Google Chat com sucesso")
        return True

    except requests.exceptions.RequestException as erro:
        logger.error(f"Erro ao enviar mensagem para o Google Chat: {erro}")
        return False


def _enviar_card(titulo: str, subtitulo: str, secoes: list) -> bool:
    """
    Envia uma mensagem com card formatado para o Google Chat.

    Args:
        titulo: Título do card
        subtitulo: Subtítulo do card
        secoes: Lista de seções com widgets

    Returns:
        True se o card foi enviado com sucesso
    """
    if not GOOGLE_CHAT_WEBHOOK_URL:
        logger.debug("Webhook do Google Chat não configurado — notificação ignorada")
        return False

    corpo = {
        "cardsV2": [
            {
                "cardId": "backup-notificacao",
                "card": {
                    "header": {
                        "title": titulo,
                        "subtitle": subtitulo,
                    },
                    "sections": secoes,
                },
            }
        ]
    }

    try:
        resposta = requests.post(
            GOOGLE_CHAT_WEBHOOK_URL,
            json=corpo,
            timeout=_TIMEOUT,
        )
        resposta.raise_for_status()
        logger.debug("Card enviado ao Google Chat com sucesso")
        return True

    except requests.exceptions.RequestException as erro:
        logger.error(f"Erro ao enviar card para o Google Chat: {erro}")
        return False


def notificar_inicio(email: str, ticket_id: str, nome: str = None) -> bool:
    """Notifica o Google Chat que um backup foi iniciado."""
    identificador = nome or email
    return _enviar_card(
        titulo="🔄 Backup Iniciado",
        subtitulo=f"Colaborador: {identificador}",
        secoes=[
            {
                "widgets": [
                    {"decoratedText": {"topLabel": "E-mail", "text": email}},
                    {"decoratedText": {"topLabel": "Ticket", "text": ticket_id}},
                    {"decoratedText": {"topLabel": "Status", "text": "Processamento iniciado"}},
                ]
            }
        ],
    )


def notificar_progresso(email: str, ticket_id: str, etapa: int,
                        descricao: str, nome: str = None) -> bool:
    """Notifica o Google Chat sobre o progresso de uma etapa."""
    identificador = nome or email
    return _enviar_card(
        titulo=f"⏳ Etapa {etapa}/8 — {descricao}",
        subtitulo=f"Colaborador: {identificador}",
        secoes=[
            {
                "widgets": [
                    {"decoratedText": {"topLabel": "E-mail", "text": email}},
                    {"decoratedText": {"topLabel": "Ticket", "text": ticket_id}},
                    {"decoratedText": {"topLabel": "Progresso", "text": f"Etapa {etapa} de 8"}},
                ]
            }
        ],
    )


def notificar_sucesso(email: str, ticket_id: str, link_drive: str,
                      nome: str = None) -> bool:
    """Notifica o Google Chat que o backup foi concluído com sucesso."""
    identificador = nome or email
    return _enviar_card(
        titulo="✅ Backup Concluído",
        subtitulo=f"Colaborador: {identificador}",
        secoes=[
            {
                "widgets": [
                    {"decoratedText": {"topLabel": "E-mail", "text": email}},
                    {"decoratedText": {"topLabel": "Ticket", "text": ticket_id}},
                    {"decoratedText": {"topLabel": "Status", "text": "Backup concluído com sucesso"}},
                    {"decoratedText": {"topLabel": "Link do Backup", "text": link_drive}},
                ]
            }
        ],
    )


def notificar_erro(email: str, ticket_id: str, descricao_erro: str,
                   nome: str = None) -> bool:
    """Notifica o Google Chat que houve erro no backup."""
    identificador = nome or email
    return _enviar_card(
        titulo="❌ Erro no Backup",
        subtitulo=f"Colaborador: {identificador}",
        secoes=[
            {
                "widgets": [
                    {"decoratedText": {"topLabel": "E-mail", "text": email}},
                    {"decoratedText": {"topLabel": "Ticket", "text": ticket_id}},
                    {"decoratedText": {"topLabel": "Erro", "text": descricao_erro}},
                    {
                        "decoratedText": {
                            "topLabel": "Ação Necessária",
                            "text": "Verificar logs e tentar novamente",
                        }
                    },
                ]
            }
        ],
    )


def notificar_vault_reaproveitado(
    email: str,
    ticket_id: str,
    email_reaproveitado: bool,
    drive_reaproveitado: bool,
    export_email_id: str = None,
    export_drive_id: str = None,
    nome: str = None,
) -> bool:
    """
    Alerta o Google Chat que exports existentes do Vault foram reaproveitados.

    Isso indica que uma execução anterior falhou após criar os exports
    (ex: erro no download, compactação ou upload) e o sistema está
    retomando o processo sem recriar os exports.

    Args:
        email: E-mail do colaborador
        ticket_id: Chave do ticket no Jira
        email_reaproveitado: True se o export de e-mail foi reaproveitado
        drive_reaproveitado: True se o export de Drive foi reaproveitado
        export_email_id: ID do export de e-mail reaproveitado
        export_drive_id: ID do export de Drive reaproveitado
        nome: Nome do colaborador (opcional)

    Returns:
        True se o alerta foi enviado com sucesso
    """
    identificador = nome or email

    if email_reaproveitado and drive_reaproveitado:
        exports_descricao = "E-mail e Drive"
    elif email_reaproveitado:
        exports_descricao = "E-mail"
    else:
        exports_descricao = "Drive"

    widgets = [
        {"decoratedText": {"topLabel": "Colaborador", "text": identificador}},
        {"decoratedText": {"topLabel": "E-mail", "text": email}},
        {"decoratedText": {"topLabel": "Ticket", "text": ticket_id}},
        {"decoratedText": {"topLabel": "Exports reaproveitados", "text": exports_descricao}},
    ]

    if email_reaproveitado and export_email_id:
        widgets.append(
            {"decoratedText": {"topLabel": "ID Export E-mail", "text": export_email_id}}
        )
    if drive_reaproveitado and export_drive_id:
        widgets.append(
            {"decoratedText": {"topLabel": "ID Export Drive", "text": export_drive_id}}
        )

    widgets.append(
        {
            "decoratedText": {
                "topLabel": "Motivo",
                "text": (
                    "Exports já existiam no Vault (execução anterior falhou). "
                    "O processo continuará usando os exports existentes."
                ),
            }
        }
    )

    return _enviar_card(
        titulo="⚠️ Vault: Exports Existentes Detectados",
        subtitulo=f"Reaproveitando export(s) de {exports_descricao}",
        secoes=[{"widgets": widgets}],
    )


def notificar_conta_excluida(email: str, ticket_id: str, nome: str = None) -> bool:
    """Notifica o Google Chat que a conta foi excluída."""
    identificador = nome or email
    return _enviar_card(
        titulo="🗑️ Conta Excluída",
        subtitulo=f"Colaborador: {identificador}",
        secoes=[
            {
                "widgets": [
                    {"decoratedText": {"topLabel": "E-mail", "text": email}},
                    {"decoratedText": {"topLabel": "Ticket", "text": ticket_id}},
                    {
                        "decoratedText": {
                            "topLabel": "Status",
                            "text": "Conta excluída do Google Workspace",
                        }
                    },
                ]
            }
        ],
    )
