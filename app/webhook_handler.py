"""
============================================================
Módulo de Tratamento do Webhook — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-02-19
Descrição: Valida e processa o payload recebido do webhook
           do Jira Service Management. Extrai as informações
           necessárias (e-mail, ticket ID) e verifica a
           autenticidade da requisição.
============================================================
Histórico:
  1.0.0 (2026-02-19) — Versão inicial
============================================================
"""

import hmac
import hashlib

from config.configuracoes import JIRA_WEBHOOK_SEGREDO
from utils.validacoes import validar_payload_webhook, extrair_email_descricao, extrair_nome_descricao
from utils.logger import obter_logger

logger = obter_logger("webhook_handler")


def validar_segredo_webhook(dados_brutos: bytes, assinatura_recebida: str) -> bool:
    """
    Valida a autenticidade do webhook comparando a assinatura HMAC.

    O Jira pode ser configurado para enviar um header com uma assinatura
    HMAC-SHA256 dos dados do webhook. Esta função compara essa assinatura
    com uma calculada localmente usando o segredo compartilhado.

    Se nenhum segredo estiver configurado (JIRA_WEBHOOK_SEGREDO vazio),
    a validação é pulada (útil para desenvolvimento/testes).

    Args:
        dados_brutos: Corpo da requisição HTTP em bytes
        assinatura_recebida: Valor do header de assinatura enviado pelo Jira

    Returns:
        True se a assinatura é válida ou se a validação está desabilitada
    """
    # Se não há segredo configurado, pula a validação
    if not JIRA_WEBHOOK_SEGREDO:
        logger.warning(
            "Validação de segredo do webhook DESABILITADA. "
            "Configure JIRA_WEBHOOK_SEGREDO no .env para produção!"
        )
        return True

    if not assinatura_recebida:
        logger.warning("Requisição sem assinatura de webhook")
        return False

    # Calcula a assinatura HMAC-SHA256 usando o segredo compartilhado
    assinatura_calculada = hmac.new(
        JIRA_WEBHOOK_SEGREDO.encode("utf-8"),
        dados_brutos,
        hashlib.sha256,
    ).hexdigest()

    # Comparação segura contra timing attacks
    valido = hmac.compare_digest(assinatura_calculada, assinatura_recebida)

    if not valido:
        logger.warning("Assinatura do webhook INVÁLIDA — possível tentativa de fraude")

    return valido


def extrair_dados_webhook(payload: dict) -> dict | None:
    """
    Extrai e valida os dados necessários do payload do webhook do Jira.

    O webhook do "Automation for Jira" envia um JSON com:
    - descricao: Texto completo da descrição do chamado SPN (campo {{issue.description}})
    - ticket_id: Chave do ticket no Jira (campo {{issue.key}}, ex: "SPN-123")

    O e-mail e o nome do colaborador são extraídos do texto da descrição
    via regex, pois o formulário do chamado SPN usa o formato:
    "...Nome colaborador: NOME COMPLETOEmail Coorporativo: email@empresa.com.br..."

    Args:
        payload: Dicionário com os dados recebidos do webhook

    Returns:
        Dicionário com os dados extraídos e validados, ou None se inválido.
        Formato retornado:
        {
            "email": "colaborador@empresa.com",
            "ticket_id": "SPN-123",
            "nome": "Nome do Colaborador"  (pode ser None)
        }
    """
    logger.info("Processando payload do webhook...")
    logger.debug(f"Payload recebido: {payload}")

    # Valida o payload e extrai o e-mail da descrição
    valido, mensagem_erro = validar_payload_webhook(payload)
    if not valido:
        logger.error(f"Payload inválido: {mensagem_erro}")
        return None

    descricao = payload["descricao"]

    dados = {
        "email": extrair_email_descricao(descricao).strip().lower(),
        "ticket_id": payload["ticket_id"].strip(),
        "nome": extrair_nome_descricao(descricao),
    }

    logger.info(
        f"Dados extraídos do webhook — "
        f"E-mail: {dados['email']}, "
        f"Ticket: {dados['ticket_id']}, "
        f"Nome: {dados['nome'] or 'Não informado'}"
    )

    return dados
