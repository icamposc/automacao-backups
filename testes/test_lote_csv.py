"""
Testes do upload em lote (app/dashboard.py).

Cobre o parser _extrair_registros_csv (e-mail obrigatório, nome e chamado
opcionais) e o endpoint POST /api/backups/lote:
- chamado + nome reais informados → repassados ao enfileiramento;
- sem chamado → mantém o comportamento atual (ticket sintético LOTE-*, nome None);
- compatibilidade com o formato antigo (coluna única / cabeçalho 'email').
"""

import io
from unittest.mock import patch

import pytest
from flask import Flask

import app.dashboard as dash


# ─── Parser puro ───────────────────────────────────────────────────────────

def test_parser_le_email_nome_chamado():
    csv_data = (
        "email,nome,chamado\n"
        "fulano@madeiramadeira.com.br,Fulano de Tal,SPN-12345\n"
    )
    regs = dash._extrair_registros_csv(csv_data)
    assert regs == [
        {"email": "fulano@madeiramadeira.com.br", "nome": "Fulano de Tal", "ticket_id": "SPN-12345"}
    ]


def test_parser_chamado_e_nome_vazios_viram_none():
    csv_data = "email,nome,chamado\nfulano@madeiramadeira.com.br,,\n"
    regs = dash._extrair_registros_csv(csv_data)
    assert regs == [
        {"email": "fulano@madeiramadeira.com.br", "nome": None, "ticket_id": None}
    ]


def test_parser_chamado_normalizado_para_maiusculo():
    regs = dash._extrair_registros_csv("email,chamado\na@b.com,spn-9\n")
    assert regs[0]["ticket_id"] == "SPN-9"


def test_parser_compat_coluna_unica_sem_cabecalho():
    regs = dash._extrair_registros_csv("a@b.com\nc@d.com\n")
    assert regs == [
        {"email": "a@b.com", "nome": None, "ticket_id": None},
        {"email": "c@d.com", "nome": None, "ticket_id": None},
    ]


def test_parser_compat_cabecalho_email_apenas():
    regs = dash._extrair_registros_csv("email\na@b.com\n")
    assert regs == [{"email": "a@b.com", "nome": None, "ticket_id": None}]


def test_parser_aliases_de_cabecalho():
    csv_data = "e-mail,colaborador,ticket\na@b.com,Ana,SPN-1\n"
    regs = dash._extrair_registros_csv(csv_data)
    assert regs == [{"email": "a@b.com", "nome": "Ana", "ticket_id": "SPN-1"}]


def test_parser_dedup_por_email_mantem_primeira_ocorrencia():
    csv_data = (
        "email,nome,chamado\n"
        "a@b.com,Primeiro,SPN-1\n"
        "a@b.com,Segundo,SPN-2\n"
    )
    regs = dash._extrair_registros_csv(csv_data)
    assert regs == [{"email": "a@b.com", "nome": "Primeiro", "ticket_id": "SPN-1"}]


def test_parser_ignora_comentarios_e_linhas_em_branco():
    csv_data = "# instrucoes\n\nemail,nome,chamado\na@b.com,Ana,SPN-1\n\n"
    regs = dash._extrair_registros_csv(csv_data)
    assert regs == [{"email": "a@b.com", "nome": "Ana", "ticket_id": "SPN-1"}]


# ─── Endpoint ──────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    app_ = Flask(__name__)
    app_.register_blueprint(dash.bp)
    return app_.test_client()


def _post_csv(client, conteudo: str):
    return client.post(
        "/api/backups/lote",
        data={"arquivo": (io.BytesIO(conteudo.encode("utf-8")), "lote.csv")},
        content_type="multipart/form-data",
    )


def test_endpoint_usa_chamado_e_nome_reais(client):
    csv_data = "email,nome,chamado\nfulano@madeiramadeira.com.br,Fulano,SPN-123\n"
    with patch.object(dash, "esta_em_processamento", return_value=False), \
         patch.object(dash, "iniciar_backup_async") as mock_init:
        resp = _post_csv(client, csv_data)

    assert resp.status_code == 200
    mock_init.assert_called_once_with(
        "fulano@madeiramadeira.com.br", "SPN-123", "Fulano", deletar_conta=True
    )


def test_endpoint_fallback_lote_quando_sem_chamado(client):
    csv_data = "email\nfulano@madeiramadeira.com.br\n"
    with patch.object(dash, "esta_em_processamento", return_value=False), \
         patch.object(dash, "iniciar_backup_async") as mock_init:
        resp = _post_csv(client, csv_data)

    assert resp.status_code == 200
    args, kwargs = mock_init.call_args
    assert args[0] == "fulano@madeiramadeira.com.br"
    assert args[1].startswith("LOTE-")   # ticket sintético, como antes
    assert args[2] is None               # nome None
    assert kwargs == {"deletar_conta": True}
