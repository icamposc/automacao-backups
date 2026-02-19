"""
============================================================
Testes do Webhook — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-02-19
Descrição: Testes unitários para validação de payloads,
           extração de dados e rotas do webhook.

           Uso: pytest testes/teste_webhook.py -v
============================================================
Histórico:
  1.0.0 (2026-02-19) — Versão inicial
============================================================
"""

import pytest
from utils.validacoes import validar_email, validar_payload_webhook
from app.webhook_handler import extrair_dados_webhook


# ============================================================
# Testes de validação de e-mail
# ============================================================

class TestValidarEmail:
    """Testes para a função validar_email."""

    def test_email_valido_simples(self):
        """E-mail com formato padrão deve ser válido."""
        assert validar_email("usuario@empresa.com") is True

    def test_email_valido_com_ponto(self):
        """E-mail com pontos no nome do usuário deve ser válido."""
        assert validar_email("nome.sobrenome@empresa.com") is True

    def test_email_valido_com_subdominio(self):
        """E-mail com subdomínio deve ser válido."""
        assert validar_email("usuario@mail.empresa.com.br") is True

    def test_email_vazio(self):
        """E-mail vazio deve ser inválido."""
        assert validar_email("") is False

    def test_email_none(self):
        """E-mail None deve ser inválido."""
        assert validar_email(None) is False

    def test_email_sem_arroba(self):
        """E-mail sem @ deve ser inválido."""
        assert validar_email("usuario.empresa.com") is False

    def test_email_sem_dominio(self):
        """E-mail sem domínio completo deve ser inválido."""
        assert validar_email("usuario@") is False

    def test_email_so_espacos(self):
        """E-mail com apenas espaços deve ser inválido."""
        assert validar_email("   ") is False


# ============================================================
# Testes de validação de payload
# ============================================================

class TestValidarPayload:
    """Testes para a função validar_payload_webhook."""

    def test_payload_valido_completo(self):
        """Payload com todos os campos obrigatórios deve ser válido."""
        payload = {
            "email_colaborador": "usuario@empresa.com",
            "ticket_id": "SPN-123",
        }
        valido, erro = validar_payload_webhook(payload)
        assert valido is True
        assert erro == ""

    def test_payload_sem_email(self):
        """Payload sem e-mail deve ser inválido."""
        payload = {"ticket_id": "SPN-123"}
        valido, erro = validar_payload_webhook(payload)
        assert valido is False
        assert "email_colaborador" in erro

    def test_payload_sem_ticket(self):
        """Payload sem ticket_id deve ser inválido."""
        payload = {"email_colaborador": "usuario@empresa.com"}
        valido, erro = validar_payload_webhook(payload)
        assert valido is False
        assert "ticket_id" in erro

    def test_payload_email_invalido(self):
        """Payload com e-mail inválido deve ser rejeitado."""
        payload = {
            "email_colaborador": "nao-e-email",
            "ticket_id": "SPN-123",
        }
        valido, erro = validar_payload_webhook(payload)
        assert valido is False

    def test_payload_vazio(self):
        """Payload vazio deve ser inválido."""
        valido, erro = validar_payload_webhook({})
        assert valido is False

    def test_payload_none(self):
        """Payload None deve ser inválido."""
        valido, erro = validar_payload_webhook(None)
        assert valido is False


# ============================================================
# Testes de extração de dados do webhook
# ============================================================

class TestExtrairDadosWebhook:
    """Testes para a função extrair_dados_webhook."""

    def test_extracao_completa(self):
        """Deve extrair todos os campos corretamente."""
        payload = {
            "email_colaborador": "  Usuario@Empresa.com  ",
            "ticket_id": "  SPN-123  ",
            "nome_colaborador": "  Nome Completo  ",
        }
        dados = extrair_dados_webhook(payload)

        assert dados is not None
        assert dados["email"] == "usuario@empresa.com"  # Convertido para minúsculo e sem espaços
        assert dados["ticket_id"] == "SPN-123"
        assert dados["nome"] == "Nome Completo"

    def test_extracao_sem_nome(self):
        """Deve funcionar sem o campo de nome (opcional)."""
        payload = {
            "email_colaborador": "usuario@empresa.com",
            "ticket_id": "SPN-123",
        }
        dados = extrair_dados_webhook(payload)

        assert dados is not None
        assert dados["nome"] is None

    def test_extracao_payload_invalido(self):
        """Deve retornar None para payload inválido."""
        payload = {"campo_errado": "valor"}
        dados = extrair_dados_webhook(payload)
        assert dados is None
