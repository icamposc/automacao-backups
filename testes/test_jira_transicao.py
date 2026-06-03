"""
Testes de servicos/jira_atualizacao.transicionar_para_status

Cobre o bug em que a transição para "Resolvido" não era encontrada porque o
matching comparava com o nome do status-alvo (to.name = "RESOLVIDO", em caixa
alta no workflow do SPN) em vez do nome da transição ("Resolvido"). A correção
torna a comparação case-insensitive e aceita tanto o nome da transição quanto
o nome do status-alvo.
"""

from unittest.mock import patch

import servicos.jira_atualizacao as jira


# Transições como o Jira Cloud do SPN as devolve: o nome da transição é
# "Resolvido", mas o status-alvo (to.name) vem em CAIXA ALTA.
_TRANSICOES_SPN = [
    {"id": "381", "name": "Resolvido", "to": {"name": "RESOLVIDO"}},
    {"id": "391", "name": "Cancelado", "to": {"name": "Cancelado"}},
    {"id": "401", "name": "Aguardando usuário", "to": {"name": "Aguardando usuário"}},
    {"id": "501", "name": "Trabalho em andamento", "to": {"name": "Em Análise"}},
]


def test_transiciona_resolvido_apesar_de_status_alvo_em_caixa_alta():
    """Deve encontrar a transição 381 mesmo com to.name='RESOLVIDO'."""
    with patch.object(jira, "obter_status_atual", return_value="Aguardando Suporte"), \
         patch.object(jira, "obter_transicoes_disponiveis", return_value=_TRANSICOES_SPN), \
         patch.object(jira, "transicionar_ticket", return_value=True) as mock_transicao:
        ok = jira.transicionar_para_status("SPN-1", "Resolvido", campos={"x": 1})

    assert ok is True
    mock_transicao.assert_called_once_with("SPN-1", "381", campos={"x": 1})


def test_casa_pelo_nome_da_transicao():
    """Casa pelo nome da transição quando o status-alvo difere (ex.: 'Em Análise')."""
    with patch.object(jira, "obter_status_atual", return_value="Aguardando Suporte"), \
         patch.object(jira, "obter_transicoes_disponiveis", return_value=_TRANSICOES_SPN), \
         patch.object(jira, "transicionar_ticket", return_value=True) as mock_transicao:
        ok = jira.transicionar_para_status("SPN-2", "Trabalho em andamento")

    assert ok is True
    mock_transicao.assert_called_once_with("SPN-2", "501", campos=None)


def test_ignora_quando_ja_esta_no_destino_com_caixa_diferente():
    """status_atual='RESOLVIDO' e destino='Resolvido' → no-op, sem transicionar."""
    with patch.object(jira, "obter_status_atual", return_value="RESOLVIDO"), \
         patch.object(jira, "obter_transicoes_disponiveis") as mock_lista, \
         patch.object(jira, "transicionar_ticket") as mock_transicao:
        ok = jira.transicionar_para_status("SPN-3", "Resolvido")

    assert ok is True
    mock_lista.assert_not_called()
    mock_transicao.assert_not_called()


def test_retorna_false_quando_transicao_realmente_inexistente():
    """Sem transição correspondente, retorna False e não chama transicionar_ticket."""
    transicoes = [{"id": "391", "name": "Cancelado", "to": {"name": "Cancelado"}}]
    with patch.object(jira, "obter_status_atual", return_value="Aguardando Suporte"), \
         patch.object(jira, "obter_transicoes_disponiveis", return_value=transicoes), \
         patch.object(jira, "transicionar_ticket") as mock_transicao:
        ok = jira.transicionar_para_status("SPN-4", "Resolvido")

    assert ok is False
    mock_transicao.assert_not_called()
