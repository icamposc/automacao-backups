"""
============================================================
Módulo de Validações — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-02-19
Descrição: Funções de validação usadas em todo o projeto.
           Valida e-mails, payloads do webhook, etc.
============================================================
Histórico:
  1.0.0 (2026-02-19) — Versão inicial
============================================================
"""

import re
from utils.logger import obter_logger

logger = obter_logger("validacoes")


def extrair_email_descricao(descricao: str) -> str | None:
    """
    Extrai o e-mail corporativo do texto da descrição do chamado SPN.

    A descrição é uma sequência contínua sem quebras de linha entre campos,
    no formato: ...Email Coorporativo: colaborador@empresa.com.brEmail pessoal:...

    O TLD usa [a-z]{2,6} (minúsculo) para interromper o match no início
    do próximo campo, que sempre começa com letra maiúscula.

    Args:
        descricao: Texto completo da descrição do chamado SPN

    Returns:
        E-mail extraído ou None se não encontrado
    """
    if not descricao or not isinstance(descricao, str):
        return None
    padrao = r'Email Coorporativo:\s*([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-z]{2,6})'
    match = re.search(padrao, descricao)
    return match.group(1).strip() if match else None


def extrair_nome_descricao(descricao: str) -> str | None:
    """
    Extrai o nome do colaborador do texto da descrição do chamado SPN.

    Args:
        descricao: Texto completo da descrição do chamado SPN

    Returns:
        Nome extraído ou None se não encontrado
    """
    if not descricao or not isinstance(descricao, str):
        return None
    padrao = r'Nome colaborador:\s*(.+?)(?=Email|$)'
    match = re.search(padrao, descricao)
    return match.group(1).strip() if match else None


def validar_email(email: str) -> bool:
    """
    Verifica se uma string é um endereço de e-mail válido.

    Usa uma expressão regular simples que cobre a maioria dos casos.
    Não tenta validar todos os casos possíveis do RFC 5322,
    apenas garante que o formato básico está correto.

    Args:
        email: String que pode ser um endereço de e-mail

    Returns:
        True se o formato é válido, False caso contrário
    """
    if not email or not isinstance(email, str):
        logger.warning("E-mail vazio ou tipo inválido recebido")
        return False

    # Padrão básico: algo@algo.algo
    padrao = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    valido = bool(re.match(padrao, email.strip()))

    if not valido:
        logger.warning(f"E-mail com formato inválido: {email}")

    return valido


def validar_payload_webhook(payload: dict) -> tuple[bool, str]:
    """
    Valida o payload recebido do webhook do Jira.

    O webhook do Jira deve enviar um JSON contendo pelo menos
    o e-mail do colaborador desligado e o ID do ticket.

    Args:
        payload: Dicionário com os dados do webhook

    Returns:
        Tupla (valido, mensagem_erro):
        - (True, "") se o payload é válido
        - (False, "descrição do erro") se é inválido
    """
    if not payload or not isinstance(payload, dict):
        return False, "Payload vazio ou não é um dicionário JSON"

    # Verifica se o campo de descrição existe
    descricao = payload.get("descricao")
    if not descricao:
        return False, "Campo 'descricao' ausente no payload"

    # Extrai e valida o e-mail a partir da descrição
    email = extrair_email_descricao(descricao)
    if not email:
        return False, "Campo 'Email Coorporativo' não encontrado na descrição"

    if not validar_email(email):
        return False, f"E-mail extraído com formato inválido: {email}"

    # Verifica se o ID do ticket existe
    ticket_id = payload.get("ticket_id")
    if not ticket_id:
        return False, "Campo 'ticket_id' ausente no payload"

    logger.info(f"Payload validado com sucesso — E-mail: {email}, Ticket: {ticket_id}")
    return True, ""
