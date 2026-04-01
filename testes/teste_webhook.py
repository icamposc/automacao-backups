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
from utils.validacoes import validar_email, validar_payload_webhook, extrair_email_descricao, extrair_nome_descricao
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
# Testes de extração de e-mail e nome da descrição
# ============================================================

DESCRICAO_PADRAO = (
    "Dados Colaborador Desligado:"
    "Nome colaborador: JOÃO DA SILVAEmail Coorporativo: joao.silva@empresa.com.br"
    "Email pessoal: joao@gmail.comEmail do Gestor: gestor@empresa.com.br"
    "Empresa: EmpresaCargo: ANALISTADiretoria: TIData Demissão: 2026-04-01"
)


class TestExtrairEmailDescricao:
    """Testes para a função extrair_email_descricao."""

    def test_extrai_email_formato_padrao(self):
        """Deve extrair o e-mail corporativo corretamente."""
        assert extrair_email_descricao(DESCRICAO_PADRAO) == "joao.silva@empresa.com.br"

    def test_nao_captura_email_pessoal(self):
        """Deve retornar apenas o e-mail corporativo, não o pessoal."""
        email = extrair_email_descricao(DESCRICAO_PADRAO)
        assert email != "joao@gmail.com"

    def test_descricao_vazia(self):
        """Descrição vazia deve retornar None."""
        assert extrair_email_descricao("") is None

    def test_descricao_none(self):
        """Descrição None deve retornar None."""
        assert extrair_email_descricao(None) is None

    def test_sem_campo_email_coorporativo(self):
        """Descrição sem o campo 'Email Coorporativo' deve retornar None."""
        assert extrair_email_descricao("Nome colaborador: Fulano") is None


class TestExtrairNomeDescricao:
    """Testes para a função extrair_nome_descricao."""

    def test_extrai_nome_formato_padrao(self):
        """Deve extrair o nome do colaborador corretamente."""
        assert extrair_nome_descricao(DESCRICAO_PADRAO) == "JOÃO DA SILVA"

    def test_descricao_sem_nome(self):
        """Descrição sem o campo 'Nome colaborador' deve retornar None."""
        assert extrair_nome_descricao("Email Coorporativo: joao@empresa.com") is None


# ============================================================
# Testes de validação de payload
# ============================================================

class TestValidarPayload:
    """Testes para a função validar_payload_webhook."""

    def test_payload_valido(self):
        """Payload com descrição e ticket_id deve ser válido."""
        payload = {
            "descricao": DESCRICAO_PADRAO,
            "ticket_id": "SPN-123",
        }
        valido, erro = validar_payload_webhook(payload)
        assert valido is True
        assert erro == ""

    def test_payload_sem_descricao(self):
        """Payload sem descrição deve ser inválido."""
        payload = {"ticket_id": "SPN-123"}
        valido, erro = validar_payload_webhook(payload)
        assert valido is False
        assert "descricao" in erro

    def test_payload_sem_ticket(self):
        """Payload sem ticket_id deve ser inválido."""
        payload = {"descricao": DESCRICAO_PADRAO}
        valido, erro = validar_payload_webhook(payload)
        assert valido is False
        assert "ticket_id" in erro

    def test_payload_descricao_sem_email(self):
        """Payload com descrição sem 'Email Coorporativo' deve ser rejeitado."""
        payload = {
            "descricao": "Nome colaborador: FulanoEmpresa: XYZ",
            "ticket_id": "SPN-123",
        }
        valido, erro = validar_payload_webhook(payload)
        assert valido is False
        assert "Email Coorporativo" in erro

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
        """Deve extrair e-mail e nome corretamente da descrição."""
        payload = {
            "descricao": DESCRICAO_PADRAO,
            "ticket_id": "  SPN-123  ",
        }
        dados = extrair_dados_webhook(payload)

        assert dados is not None
        assert dados["email"] == "joao.silva@empresa.com.br"
        assert dados["ticket_id"] == "SPN-123"
        assert dados["nome"] == "JOÃO DA SILVA"

    def test_email_convertido_para_minusculo(self):
        """E-mail extraído deve ser convertido para minúsculo."""
        descricao = (
            "Nome colaborador: FulanoEmail Coorporativo: Fulano@Empresa.com.br"
            "Email pessoal: f@gmail.com"
        )
        payload = {"descricao": descricao, "ticket_id": "SPN-999"}
        dados = extrair_dados_webhook(payload)

        assert dados is not None
        assert dados["email"] == "fulano@empresa.com.br"

    def test_extracao_payload_invalido(self):
        """Deve retornar None para payload inválido."""
        dados = extrair_dados_webhook({"campo_errado": "valor"})
        assert dados is None
