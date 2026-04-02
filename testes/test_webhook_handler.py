"""
Testes do módulo webhook_handler.py

Verifica:
- Validação de assinatura HMAC-SHA256
- Extração de dados do payload
- Cenários de payload inválido
"""

import hmac
import hashlib
import pytest

from app.webhook_handler import validar_segredo_webhook, extrair_dados_webhook


PAYLOAD_VALIDO = {
    "descricao": "Nome colaborador: João SilvaEmail Coorporativo: joao.silva@empresa.comEmail pessoal: joao@gmail.com",
    "ticket_id": "SPN-999",
}


class TestValidarSegredoWebhook:
    def test_sem_segredo_configurado_aceita_qualquer_coisa(self, mocker):
        mocker.patch("app.webhook_handler.JIRA_WEBHOOK_SEGREDO", "")
        assert validar_segredo_webhook(b"corpo", "qualquer") is True

    def test_assinatura_valida(self, mocker):
        segredo = "meu-segredo"
        mocker.patch("app.webhook_handler.JIRA_WEBHOOK_SEGREDO", segredo)
        corpo = b'{"teste": 1}'
        assinatura = hmac.new(segredo.encode(), corpo, hashlib.sha256).hexdigest()
        assert validar_segredo_webhook(corpo, assinatura) is True

    def test_assinatura_invalida(self, mocker):
        mocker.patch("app.webhook_handler.JIRA_WEBHOOK_SEGREDO", "segredo")
        assert validar_segredo_webhook(b"corpo", "assinatura-errada") is False

    def test_sem_assinatura_com_segredo(self, mocker):
        mocker.patch("app.webhook_handler.JIRA_WEBHOOK_SEGREDO", "segredo")
        assert validar_segredo_webhook(b"corpo", "") is False


class TestExtrairDadosWebhook:
    def test_extrai_email_e_ticket(self):
        dados = extrair_dados_webhook(PAYLOAD_VALIDO)
        assert dados is not None
        assert dados["email"] == "joao.silva@empresa.com"
        assert dados["ticket_id"] == "SPN-999"

    def test_extrai_nome(self):
        dados = extrair_dados_webhook(PAYLOAD_VALIDO)
        assert dados["nome"] == "João Silva"

    def test_payload_vazio_retorna_none(self):
        assert extrair_dados_webhook({}) is None

    def test_sem_email_retorna_none(self):
        payload = {"descricao": "Sem campo de email aqui", "ticket_id": "SPN-1"}
        assert extrair_dados_webhook(payload) is None

    def test_sem_ticket_id_retorna_none(self):
        payload = {"descricao": PAYLOAD_VALIDO["descricao"]}
        assert extrair_dados_webhook(payload) is None

    def test_email_normalizado_lowercase(self):
        # A regex em validacoes.py aceita apenas TLD minúsculo (por design)
        # então o e-mail no campo deve ter TLD minúsculo para ser extraído
        payload = {
            "descricao": "Nome colaborador: Teste UsrEmail Coorporativo: TEST.USER@empresa.comEmail pessoal: x@x.com",
            "ticket_id": "SPN-1",
        }
        dados = extrair_dados_webhook(payload)
        assert dados is not None
        assert dados["email"] == "test.user@empresa.com"
