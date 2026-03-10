"""
============================================================
Módulo de Atualização do Jira — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-02-19
Descrição: Gerencia a comunicação com o Jira Service Management
           via API REST v3. Adiciona comentários e transiciona
           tickets durante o processo de backup.

           Falhas na atualização do Jira NUNCA devem impedir
           o fluxo principal de backup — são apenas registradas
           nos logs.
============================================================
Histórico:
  1.0.0 (2026-02-19) — Versão inicial
============================================================
"""

import requests
from requests.auth import HTTPBasicAuth

from config.configuracoes import JIRA_URL_BASE, JIRA_EMAIL, JIRA_API_TOKEN
from utils.logger import obter_logger

logger = obter_logger("jira_atualizacao")

# Cabeçalhos padrão para a API do Jira
_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
}

# Autenticação básica do Jira (e-mail + API token)
_AUTH = HTTPBasicAuth(JIRA_EMAIL, JIRA_API_TOKEN)

# Timeout para requisições HTTP ao Jira (segundos)
_TIMEOUT = 30


def _adicionar_comentario(ticket_id: str, mensagem: str) -> bool:
    """
    Adiciona um comentário a um ticket no Jira.

    Usa a API REST v3 do Jira com formato ADF (Atlassian Document Format)
    para o corpo do comentário.

    Args:
        ticket_id: ID ou chave do ticket (ex: "SPN-123")
        mensagem: Texto do comentário a ser adicionado

    Returns:
        True se o comentário foi adicionado, False se houve erro
    """
    url = f"{JIRA_URL_BASE}/rest/api/3/issue/{ticket_id}/comment"

    # Corpo no formato ADF (Atlassian Document Format) — obrigatório na API v3
    corpo = {
        "body": {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": mensagem,
                        }
                    ],
                }
            ],
        }
    }

    try:
        resposta = requests.post(
            url, json=corpo, headers=_HEADERS, auth=_AUTH, timeout=_TIMEOUT
        )
        resposta.raise_for_status()
        logger.info(f"Comentário adicionado ao ticket {ticket_id}")
        return True

    except requests.exceptions.RequestException as erro:
        logger.error(f"Erro ao comentar no ticket {ticket_id}: {erro}")
        return False


def comentar_inicio(ticket_id: str, email: str) -> bool:
    """
    Adiciona comentário informando que o backup foi iniciado.

    Args:
        ticket_id: Chave do ticket no Jira (ex: "SPN-123")
        email: E-mail do colaborador sendo processado

    Returns:
        True se o comentário foi adicionado com sucesso
    """
    mensagem = (
        f"[Automação] Backup iniciado para: {email}\n\n"
        f"Etapas em andamento:\n"
        f"1. Criando exportação de E-mails no Google Vault\n"
        f"2. Criando exportação do Drive no Google Vault\n\n"
        f"O processo pode levar algumas horas dependendo do volume de dados. "
        f"Atualizações serão postadas automaticamente neste ticket."
    )
    return _adicionar_comentario(ticket_id, mensagem)


def comentar_progresso(ticket_id: str, etapa: str) -> bool:
    """
    Adiciona comentário com atualização de progresso.

    Args:
        ticket_id: Chave do ticket no Jira
        etapa: Descrição da etapa atual do processo

    Returns:
        True se o comentário foi adicionado com sucesso
    """
    mensagem = f"[Automação] Atualização de progresso: {etapa}"
    return _adicionar_comentario(ticket_id, mensagem)


def comentar_sucesso(ticket_id: str, email: str, link_drive: str) -> bool:
    """
    Adiciona comentário informando que o backup foi concluído com sucesso.

    Inclui o link para o arquivo no Google Drive Compartilhado.

    Args:
        ticket_id: Chave do ticket no Jira
        email: E-mail do colaborador processado
        link_drive: Link do arquivo .zip no Google Drive

    Returns:
        True se o comentário foi adicionado com sucesso
    """
    mensagem = (
        f"[Automação] Backup CONCLUÍDO com sucesso para: {email}\n\n"
        f"O arquivo de backup foi enviado para o Google Drive Compartilhado.\n"
        f"Link: {link_drive}\n\n"
        f"Conteúdo do backup:\n"
        f"- E-mails (formato PST)\n"
        f"- Arquivos do Google Drive"
    )
    return _adicionar_comentario(ticket_id, mensagem)


def comentar_erro(ticket_id: str, email: str, descricao_erro: str) -> bool:
    """
    Adiciona comentário informando que houve erro no processo de backup.

    Args:
        ticket_id: Chave do ticket no Jira
        email: E-mail do colaborador sendo processado
        descricao_erro: Descrição do erro que ocorreu

    Returns:
        True se o comentário foi adicionado com sucesso
    """
    mensagem = (
        f"[Automação] ERRO no backup de: {email}\n\n"
        f"Descrição do erro: {descricao_erro}\n\n"
        f"Ação necessária: Verificar os logs do sistema e tentar novamente "
        f"se necessário. Contate a equipe de infraestrutura caso o erro persista."
    )
    return _adicionar_comentario(ticket_id, mensagem)


def comentar_conta_excluida(ticket_id: str, email: str) -> bool:
    """
    Adiciona comentário informando que a conta do colaborador foi excluída.

    Este comentário é adicionado após a confirmação de que o backup
    está no Drive Compartilhado e a conta foi deletada do Google Workspace.

    Args:
        ticket_id: Chave do ticket no Jira (ex: "SPN-123")
        email: E-mail da conta que foi excluída

    Returns:
        True se o comentário foi adicionado com sucesso
    """
    mensagem = f"[Automação] Conta excluída: {email}"
    return _adicionar_comentario(ticket_id, mensagem)


def transicionar_ticket(ticket_id: str, id_transicao: str) -> bool:
    """
    Transiciona um ticket para outro status no workflow do Jira.

    O ID da transição depende do workflow configurado no projeto Jira.
    Exemplos comuns:
    - "31" → Concluído/Done
    - "21" → Em Progresso
    (os IDs variam conforme a configuração do projeto)

    Para descobrir os IDs de transição disponíveis, use:
    GET /rest/api/3/issue/{ticket_id}/transitions

    Args:
        ticket_id: Chave do ticket no Jira (ex: "SPN-123")
        id_transicao: ID numérico da transição (string)

    Returns:
        True se a transição foi executada com sucesso
    """
    url = f"{JIRA_URL_BASE}/rest/api/3/issue/{ticket_id}/transitions"

    corpo = {
        "transition": {
            "id": id_transicao,
        }
    }

    try:
        resposta = requests.post(
            url, json=corpo, headers=_HEADERS, auth=_AUTH, timeout=_TIMEOUT
        )
        resposta.raise_for_status()
        logger.info(f"Ticket {ticket_id} transicionado com sucesso (transição: {id_transicao})")
        return True

    except requests.exceptions.RequestException as erro:
        logger.error(f"Erro ao transicionar ticket {ticket_id}: {erro}")
        return False


def obter_transicoes_disponiveis(ticket_id: str) -> list:
    """
    Lista as transições disponíveis para um ticket.

    Útil para descobrir os IDs de transição corretos para o
    workflow do projeto Jira.

    Args:
        ticket_id: Chave do ticket no Jira

    Returns:
        Lista de transições disponíveis (cada uma com 'id' e 'name'),
        ou lista vazia se houve erro
    """
    url = f"{JIRA_URL_BASE}/rest/api/3/issue/{ticket_id}/transitions"

    try:
        resposta = requests.get(
            url, headers=_HEADERS, auth=_AUTH, timeout=_TIMEOUT
        )
        resposta.raise_for_status()
        transicoes = resposta.json().get("transitions", [])

        logger.info(f"Transições disponíveis para {ticket_id}:")
        for t in transicoes:
            logger.info(f"  ID: {t['id']} — Nome: {t['name']}")

        return transicoes

    except requests.exceptions.RequestException as erro:
        logger.error(f"Erro ao buscar transições do ticket {ticket_id}: {erro}")
        return []
