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

from config.configuracoes import JIRA_URL_BASE, JIRA_EMAIL, JIRA_API_TOKEN, JIRA_CLOUD_ID
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


def comentar_inicio(ticket_id: str, email: str, deletar_conta: bool = True) -> bool:
    """
    Adiciona comentário informando que o backup foi iniciado.

    Args:
        ticket_id:     Chave do ticket no Jira (ex: "SPN-123")
        email:         E-mail do colaborador sendo processado
        deletar_conta: Se False, inclui aviso de que a conta NÃO será excluída

    Returns:
        True se o comentário foi adicionado com sucesso
    """
    aviso_conta = (
        "\n⚠️ Atenção: a exclusão automática da conta está DESATIVADA para este backup. "
        "A conta será mantida no Google Workspace ao final do processo."
        if not deletar_conta else ""
    )
    mensagem = (
        f"[Automação] Backup iniciado para: {email}\n\n"
        f"Etapas em andamento:\n"
        f"1. Criando exportação de E-mails no Google Vault\n"
        f"2. Criando exportação do Drive no Google Vault\n\n"
        f"O processo pode levar algumas horas dependendo do volume de dados. "
        f"Atualizações serão postadas automaticamente neste ticket."
        f"{aviso_conta}"
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


def comentar_sucesso(ticket_id: str, email: str, link_drive: str, deletar_conta: bool = True) -> bool:
    """
    Adiciona comentário informando que o backup foi concluído com sucesso.

    Inclui o link para o arquivo no Google Drive Compartilhado.

    Args:
        ticket_id:     Chave do ticket no Jira
        email:         E-mail do colaborador processado
        link_drive:    Link do arquivo .zip no Google Drive
        deletar_conta: Se False, registra que a conta foi mantida no Workspace

    Returns:
        True se o comentário foi adicionado com sucesso
    """
    status_conta = (
        "⚠️ Conta mantida no Google Workspace (exclusão desativada para este backup)."
        if not deletar_conta
        else "A conta será excluída na próxima etapa."
    )
    mensagem = (
        f"[Automação] Backup CONCLUÍDO com sucesso para: {email}\n\n"
        f"O arquivo de backup foi enviado para o Google Drive Compartilhado.\n"
        f"Link: {link_drive}\n\n"
        f"Conteúdo do backup:\n"
        f"- E-mails (formato PST)\n"
        f"- Arquivos do Google Drive\n\n"
        f"{status_conta}"
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


def transicionar_ticket(ticket_id: str, id_transicao: str, campos: dict = None) -> bool:
    """
    Transiciona um ticket para outro status no workflow do Jira.

    O ID da transição depende do workflow configurado no projeto Jira.
    Para descobrir os IDs de transição disponíveis, use:
    GET /rest/api/3/issue/{ticket_id}/transitions

    Args:
        ticket_id: Chave do ticket no Jira (ex: "SPN-123")
        id_transicao: ID numérico da transição (string)
        campos: Campos adicionais obrigatórios pela tela de transição (opcional)

    Returns:
        True se a transição foi executada com sucesso
    """
    url = f"{JIRA_URL_BASE}/rest/api/3/issue/{ticket_id}/transitions"

    corpo = {"transition": {"id": id_transicao}}
    if campos:
        corpo["fields"] = campos

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


# Campos obrigatórios na tela de transição "Resolvido" do projeto SPN
_CAMPOS_TRANSICAO_RESOLVIDO = {
    "resolution": {"id": "10000"},           # Done
    "customfield_12088": {"id": "29264"},    # Tipo de atividade: Suporte
    "customfield_11132": {"id": "27295"},    # sdn_time: Automação
    "customfield_24413": 0,                  # Custo de Manutenção: 0
    "customfield_11129": {"id": "24423"},    # Equipe Resolvedora: Automação
}

# Ordem dos status no fluxo de trabalho do projeto SPN.
# Usado para evitar regressão (ex: não voltar para "Em análise" se já está em "Escalado").
_ORDEM_FLUXO = ["Aguardando Suporte", "Em análise", "Escalado", "Resolvido", "Fechado"]


def _normalizar_nome(nome: str | None) -> str:
    """Normaliza nome de status/transição para comparação robusta a caixa e espaços."""
    return (nome or "").strip().casefold()


def obter_status_atual(ticket_id: str) -> str | None:
    """
    Retorna o nome do status atual do ticket.

    Args:
        ticket_id: Chave do ticket no Jira (ex: "SPN-123")

    Returns:
        Nome do status atual, ou None se houve erro
    """
    url = f"{JIRA_URL_BASE}/rest/api/3/issue/{ticket_id}?fields=status"
    try:
        resposta = requests.get(url, headers=_HEADERS, auth=_AUTH, timeout=_TIMEOUT)
        resposta.raise_for_status()
        return resposta.json()["fields"]["status"]["name"]
    except requests.exceptions.RequestException as erro:
        logger.error(f"Erro ao obter status do ticket {ticket_id}: {erro}")
        return None


def transicionar_para_status(ticket_id: str, status_destino: str, campos: dict = None) -> bool:
    """
    Transiciona o ticket para o status destino buscando o ID de transição dinamicamente.

    Verifica o status atual antes de transicionar:
    - Se já está no status destino, ignora.
    - Se está em um status mais avançado no fluxo, ignora (evita regressão).
    - Busca o ID da transição disponível para o status destino e executa.

    Fluxo esperado: Aguardando Suporte → Em análise → Escalado → Resolvido

    Args:
        ticket_id:      Chave do ticket no Jira (ex: "SPN-123")
        status_destino: Nome exato do status destino (ex: "Em análise", "Resolvido")
        campos:         Campos obrigatórios pela tela de transição (opcional)

    Returns:
        True se a transição foi executada (ou não era necessária), False se houve erro
    """
    status_atual = obter_status_atual(ticket_id)
    if not status_atual:
        return False

    # Os nomes de status/transição do projeto SPN não têm caixa estável
    # (ex.: status-alvo "RESOLVIDO" para a transição "Resolvido", "Em Análise"
    # vs "Em análise"). Todas as comparações abaixo são case-insensitive para
    # não depender da grafia exata configurada no workflow do Jira.
    alvo = _normalizar_nome(status_destino)

    if _normalizar_nome(status_atual) == alvo:
        logger.info(f"Ticket {ticket_id} já está em '{status_destino}' — transição ignorada")
        return True

    fluxo = [_normalizar_nome(s) for s in _ORDEM_FLUXO]
    try:
        pos_atual = fluxo.index(_normalizar_nome(status_atual))
        pos_destino = fluxo.index(alvo)
        if pos_atual > pos_destino:
            logger.info(
                f"Ticket {ticket_id} está em '{status_atual}' (mais avançado que '{status_destino}') "
                f"— transição ignorada para evitar regressão no fluxo"
            )
            return True
    except ValueError:
        pass  # Status fora do fluxo conhecido — tenta transicionar normalmente

    transicoes = obter_transicoes_disponiveis(ticket_id)
    # Casa pelo nome da própria transição OU pelo nome do status-alvo (to.name),
    # ambos normalizados — o Jira do SPN nomeia a transição "Resolvido" mas o
    # status-alvo como "RESOLVIDO", e essa divergência de caixa fazia a busca
    # antiga (t["to"]["name"] == status_destino) falhar silenciosamente.
    transicao = next(
        (
            t
            for t in transicoes
            if _normalizar_nome(t.get("name")) == alvo
            or _normalizar_nome((t.get("to") or {}).get("name")) == alvo
        ),
        None,
    )

    if not transicao:
        logger.error(
            f"Transição para '{status_destino}' não disponível no ticket {ticket_id} "
            f"(status atual: '{status_atual}')"
        )
        return False

    return transicionar_ticket(ticket_id, transicao["id"], campos=campos)


def transicionar_resolvido(ticket_id: str, id_transicao: str = None) -> bool:
    """
    Transiciona o ticket para "Resolvido" preenchendo os campos obrigatórios
    da tela de transição do projeto SPN.

    Utiliza busca dinâmica de transição — funciona independente do status atual,
    incluindo quando o ticket está em "Escalado" por intervenção manual.
    O parâmetro id_transicao é mantido apenas por compatibilidade e é ignorado.

    Args:
        ticket_id:    Chave do ticket no Jira (ex: "SPN-123")
        id_transicao: Ignorado (mantido por compatibilidade)

    Returns:
        True se a transição foi executada com sucesso
    """
    return transicionar_para_status(ticket_id, "Resolvido", campos=_CAMPOS_TRANSICAO_RESOLVIDO)


def submeter_formularios_pendentes(ticket_id: str) -> bool:
    """
    Busca e submete todos os formulários pendentes (não enviados) de um ticket.

    Usa a API de Formulários do Jira Cloud (ProForma):
    - GET  https://api.atlassian.com/jira/forms/cloud/{cloudId}/issue/{issueId}/form
    - PUT  https://api.atlassian.com/jira/forms/cloud/{cloudId}/issue/{issueId}/form/{formId}/action/submit

    O header X-ExperimentalApi: opt-in é obrigatório para essa API.

    Args:
        ticket_id: Chave do ticket no Jira (ex: "SPN-123")

    Returns:
        True se todos os formulários foram submetidos (ou não havia nenhum pendente)
    """
    if not JIRA_CLOUD_ID:
        logger.warning("JIRA_CLOUD_ID não configurado — pulando submissão de formulários")
        return True

    # Primeiro obtém o ID numérico do issue (necessário para a API de Formulários)
    try:
        url_issue = f"{JIRA_URL_BASE}/rest/api/3/issue/{ticket_id}?fields=id"
        resp_issue = requests.get(url_issue, headers=_HEADERS, auth=_AUTH, timeout=_TIMEOUT)
        resp_issue.raise_for_status()
        issue_id = resp_issue.json().get("id")

        if not issue_id:
            logger.error(f"Não foi possível obter o ID numérico do ticket {ticket_id}")
            return False

        logger.info(f"ID numérico do ticket {ticket_id}: {issue_id}")

    except requests.exceptions.RequestException as erro:
        logger.error(f"Erro ao obter ID do ticket {ticket_id}: {erro}")
        return False

    # Header obrigatório para a API experimental de Formulários
    headers_forms = {
        **_HEADERS,
        "X-ExperimentalApi": "opt-in",
    }

    # Busca os formulários do issue
    url_forms = f"https://api.atlassian.com/jira/forms/cloud/{JIRA_CLOUD_ID}/issue/{issue_id}/form"
    try:
        resp_forms = requests.get(url_forms, headers=headers_forms, auth=_AUTH, timeout=_TIMEOUT)
        resp_forms.raise_for_status()
        formularios = resp_forms.json()

        if not formularios:
            logger.info(f"Nenhum formulário encontrado no ticket {ticket_id}")
            return True

        logger.info(f"Formulários encontrados no ticket {ticket_id}: {len(formularios)}")

    except requests.exceptions.RequestException as erro:
        logger.error(f"Erro ao buscar formulários do ticket {ticket_id}: {erro}")
        return False

    # Submete os formulários que ainda não foram enviados
    sucesso = True
    for form in formularios:
        form_id = form.get("id")
        form_name = form.get("name", "sem nome")
        form_status = form.get("status", "")

        # Formulários com status "submitted" já foram enviados
        if form_status == "submitted":
            logger.info(f"Formulário '{form_name}' (ID: {form_id}) já foi submetido — ignorando")
            continue

        logger.info(f"Submetendo formulário '{form_name}' (ID: {form_id}, status: {form_status})...")
        url_submit = (
            f"https://api.atlassian.com/jira/forms/cloud/{JIRA_CLOUD_ID}"
            f"/issue/{issue_id}/form/{form_id}/action/submit"
        )
        try:
            resp_submit = requests.put(
                url_submit, headers=headers_forms, auth=_AUTH, timeout=_TIMEOUT, json={}
            )
            resp_submit.raise_for_status()
            logger.info(f"Formulário '{form_name}' submetido com sucesso")

        except requests.exceptions.RequestException as erro:
            logger.error(f"Erro ao submeter formulário '{form_name}' (ID: {form_id}): {erro}")
            sucesso = False

    return sucesso


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
