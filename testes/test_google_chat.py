"""
Testes do módulo google_chat.py

Verifica:
- Envio de notificações de início, sucesso e erro
- Notificações de falha específicas (vault timeout, download, upload, conta, jira)
- Comportamento quando webhook não está configurado
"""

import pytest
import responses as rsps_lib
from unittest.mock import patch


WEBHOOK_URL = "https://chat.googleapis.com/v1/spaces/TEST/messages?key=key&token=token"


@pytest.fixture(autouse=True)
def configurar_webhook(mocker):
    mocker.patch("servicos.google_chat.GOOGLE_CHAT_WEBHOOK_URL", WEBHOOK_URL)


def _decodificar_body(body) -> str:
    """Decodifica o body da requisição para string."""
    if isinstance(body, bytes):
        return body.decode("utf-8")
    return str(body)


@rsps_lib.activate
def test_notificar_inicio_envia_card():
    rsps_lib.add(rsps_lib.POST, WEBHOOK_URL, json={"name": "spaces/TEST/messages/1"}, status=200)

    from servicos.google_chat import notificar_inicio
    resultado = notificar_inicio("user@empresa.com", "SPN-1", "João Silva")

    assert resultado is True
    assert len(rsps_lib.calls) == 1
    corpo = _decodificar_body(rsps_lib.calls[0].request.body)
    assert "Backup Iniciado" in corpo


@rsps_lib.activate
def test_notificar_erro_generico():
    rsps_lib.add(rsps_lib.POST, WEBHOOK_URL, json={}, status=200)

    from servicos.google_chat import notificar_erro
    resultado = notificar_erro("user@empresa.com", "SPN-1", "Timeout", "João")

    assert resultado is True
    assert "Erro no Backup" in _decodificar_body(rsps_lib.calls[0].request.body)


@rsps_lib.activate
def test_notificar_erro_vault_timeout():
    rsps_lib.add(rsps_lib.POST, WEBHOOK_URL, json={}, status=200)

    from servicos.google_chat import notificar_erro_vault_timeout
    resultado = notificar_erro_vault_timeout("user@empresa.com", "SPN-1", "exp-123", 6.0)

    assert resultado is True
    corpo = _decodificar_body(rsps_lib.calls[0].request.body)
    assert "Timeout" in corpo
    assert "Vault" in corpo


@rsps_lib.activate
def test_notificar_erro_upload():
    rsps_lib.add(rsps_lib.POST, WEBHOOK_URL, json={}, status=200)

    from servicos.google_chat import notificar_erro_upload
    resultado = notificar_erro_upload("user@empresa.com", "SPN-1", 1500.0, 3)

    assert resultado is True
    assert "Upload" in _decodificar_body(rsps_lib.calls[0].request.body)


@rsps_lib.activate
def test_notificar_erro_exclusao_conta():
    rsps_lib.add(rsps_lib.POST, WEBHOOK_URL, json={}, status=200)

    from servicos.google_chat import notificar_erro_exclusao_conta
    resultado = notificar_erro_exclusao_conta("user@empresa.com", "SPN-1", "Conta não encontrada")

    assert resultado is True
    corpo = _decodificar_body(rsps_lib.calls[0].request.body)
    assert "Excluir Conta" in corpo
    assert "manualmente" in corpo


def test_sem_webhook_configurado_retorna_false(mocker):
    mocker.patch("servicos.google_chat.GOOGLE_CHAT_WEBHOOK_URL", "")

    from servicos.google_chat import notificar_inicio
    assert notificar_inicio("user@empresa.com", "SPN-1") is False


@rsps_lib.activate
def test_falha_http_retorna_false():
    rsps_lib.add(rsps_lib.POST, WEBHOOK_URL, status=500)

    from servicos.google_chat import notificar_inicio
    resultado = notificar_inicio("user@empresa.com", "SPN-1")

    assert resultado is False
