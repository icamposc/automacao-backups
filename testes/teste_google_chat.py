"""
============================================================
Testes do Google Chat — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-03-10
Descrição: Testes unitários para as funções de notificação
           do Google Chat. Usa mocks para evitar chamadas
           reais ao webhook.

           Uso: pytest testes/teste_google_chat.py -v
============================================================
Histórico:
  1.0.0 (2026-03-10) — Versão inicial
============================================================
"""

from unittest.mock import patch, MagicMock
import pytest
import requests

from servicos.google_chat import (
    _enviar_mensagem,
    _enviar_card,
    notificar_inicio,
    notificar_progresso,
    notificar_sucesso,
    notificar_erro,
    notificar_conta_excluida,
)


# URL fictícia usada nos testes
_WEBHOOK_TESTE = "https://chat.googleapis.com/v1/spaces/TESTE/messages?key=FAKE&token=FAKE"


# ============================================================
# Testes de _enviar_mensagem
# ============================================================

class TestEnviarMensagem:
    """Testes para a função interna _enviar_mensagem."""

    @patch("servicos.google_chat.GOOGLE_CHAT_WEBHOOK_URL", _WEBHOOK_TESTE)
    @patch("servicos.google_chat.requests.post")
    def test_mensagem_enviada_com_sucesso(self, mock_post):
        """Deve retornar True quando o POST retorna 200."""
        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()

        resultado = _enviar_mensagem("Teste de mensagem")

        assert resultado is True
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        assert args[0] == _WEBHOOK_TESTE
        assert kwargs["json"] == {"text": "Teste de mensagem"}
        assert kwargs["timeout"] == 15

    @patch("servicos.google_chat.GOOGLE_CHAT_WEBHOOK_URL", _WEBHOOK_TESTE)
    @patch("servicos.google_chat.requests.post")
    def test_mensagem_falha_http(self, mock_post):
        """Deve retornar False quando o POST falha."""
        mock_post.side_effect = requests.exceptions.RequestException("Erro de rede")

        resultado = _enviar_mensagem("Teste")

        assert resultado is False

    @patch("servicos.google_chat.GOOGLE_CHAT_WEBHOOK_URL", "")
    def test_mensagem_sem_webhook_configurado(self):
        """Deve retornar False quando a URL do webhook está vazia."""
        resultado = _enviar_mensagem("Teste")
        assert resultado is False

    @patch("servicos.google_chat.GOOGLE_CHAT_WEBHOOK_URL", None)
    def test_mensagem_webhook_none(self):
        """Deve retornar False quando a URL do webhook é None."""
        resultado = _enviar_mensagem("Teste")
        assert resultado is False


# ============================================================
# Testes de _enviar_card
# ============================================================

class TestEnviarCard:
    """Testes para a função interna _enviar_card."""

    @patch("servicos.google_chat.GOOGLE_CHAT_WEBHOOK_URL", _WEBHOOK_TESTE)
    @patch("servicos.google_chat.requests.post")
    def test_card_enviado_com_sucesso(self, mock_post):
        """Deve enviar card formatado e retornar True."""
        mock_post.return_value = MagicMock(status_code=200)
        mock_post.return_value.raise_for_status = MagicMock()

        secoes = [{"widgets": [{"decoratedText": {"topLabel": "Teste", "text": "Valor"}}]}]
        resultado = _enviar_card("Título", "Subtítulo", secoes)

        assert resultado is True
        _, kwargs = mock_post.call_args
        corpo = kwargs["json"]
        assert "cardsV2" in corpo
        card = corpo["cardsV2"][0]["card"]
        assert card["header"]["title"] == "Título"
        assert card["header"]["subtitle"] == "Subtítulo"
        assert card["sections"] == secoes

    @patch("servicos.google_chat.GOOGLE_CHAT_WEBHOOK_URL", _WEBHOOK_TESTE)
    @patch("servicos.google_chat.requests.post")
    def test_card_falha_http(self, mock_post):
        """Deve retornar False quando o POST do card falha."""
        mock_post.side_effect = requests.exceptions.HTTPError("403 Forbidden")

        resultado = _enviar_card("Título", "Sub", [])

        assert resultado is False

    @patch("servicos.google_chat.GOOGLE_CHAT_WEBHOOK_URL", "")
    def test_card_sem_webhook(self):
        """Deve retornar False sem webhook configurado."""
        resultado = _enviar_card("Título", "Sub", [])
        assert resultado is False


# ============================================================
# Testes de notificar_inicio
# ============================================================

class TestNotificarInicio:
    """Testes para a função notificar_inicio."""

    @patch("servicos.google_chat._enviar_card")
    def test_inicio_com_nome(self, mock_card):
        """Deve enviar card com nome do colaborador no subtítulo."""
        mock_card.return_value = True

        resultado = notificar_inicio("user@empresa.com", "SPN-100", "João Silva")

        assert resultado is True
        mock_card.assert_called_once()
        args = mock_card.call_args
        assert "Backup Iniciado" in args.kwargs.get("titulo", args[0][0] if args[0] else "")
        _, kwargs = mock_card.call_args
        assert "João Silva" in kwargs.get("subtitulo", str(args))

    @patch("servicos.google_chat._enviar_card")
    def test_inicio_sem_nome(self, mock_card):
        """Deve usar o e-mail quando o nome não é informado."""
        mock_card.return_value = True

        notificar_inicio("user@empresa.com", "SPN-100")

        _, kwargs = mock_card.call_args
        assert "user@empresa.com" in kwargs["subtitulo"]

    @patch("servicos.google_chat._enviar_card")
    def test_inicio_dados_no_card(self, mock_card):
        """Deve incluir e-mail e ticket nos widgets do card."""
        mock_card.return_value = True

        notificar_inicio("user@empresa.com", "SPN-200", "Maria")

        _, kwargs = mock_card.call_args
        secoes = kwargs["secoes"]
        widgets = secoes[0]["widgets"]
        textos = [w["decoratedText"]["text"] for w in widgets]
        assert "user@empresa.com" in textos
        assert "SPN-200" in textos


# ============================================================
# Testes de notificar_progresso
# ============================================================

class TestNotificarProgresso:
    """Testes para a função notificar_progresso."""

    @patch("servicos.google_chat._enviar_card")
    def test_progresso_etapa_1(self, mock_card):
        """Deve indicar etapa 1/8 no título."""
        mock_card.return_value = True

        notificar_progresso("user@empresa.com", "SPN-100", 1, "Notificação Jira")

        _, kwargs = mock_card.call_args
        assert "1/8" in kwargs["titulo"]
        assert "Notificação Jira" in kwargs["titulo"]

    @patch("servicos.google_chat._enviar_card")
    def test_progresso_etapa_8(self, mock_card):
        """Deve indicar etapa 8/8 no título."""
        mock_card.return_value = True

        notificar_progresso("user@empresa.com", "SPN-100", 8, "Excluir Conta")

        _, kwargs = mock_card.call_args
        assert "8/8" in kwargs["titulo"]

    @patch("servicos.google_chat._enviar_card")
    def test_progresso_com_nome(self, mock_card):
        """Deve usar o nome do colaborador quando disponível."""
        mock_card.return_value = True

        notificar_progresso("user@empresa.com", "SPN-100", 3, "Monitorar", "Carlos")

        _, kwargs = mock_card.call_args
        assert "Carlos" in kwargs["subtitulo"]

    @patch("servicos.google_chat._enviar_card")
    def test_progresso_widgets(self, mock_card):
        """Deve incluir informação de progresso nos widgets."""
        mock_card.return_value = True

        notificar_progresso("user@empresa.com", "SPN-100", 5, "Compactar ZIP")

        _, kwargs = mock_card.call_args
        widgets = kwargs["secoes"][0]["widgets"]
        textos = [w["decoratedText"]["text"] for w in widgets]
        assert "Etapa 5 de 8" in textos


# ============================================================
# Testes de notificar_sucesso
# ============================================================

class TestNotificarSucesso:
    """Testes para a função notificar_sucesso."""

    @patch("servicos.google_chat._enviar_card")
    def test_sucesso_com_link(self, mock_card):
        """Deve incluir o link do Drive no card de sucesso."""
        mock_card.return_value = True
        link = "https://drive.google.com/drive/folders/abc123"

        notificar_sucesso("user@empresa.com", "SPN-100", link, "Ana")

        _, kwargs = mock_card.call_args
        assert "Concluído" in kwargs["titulo"]
        widgets = kwargs["secoes"][0]["widgets"]
        textos = [w["decoratedText"]["text"] for w in widgets]
        assert link in textos

    @patch("servicos.google_chat._enviar_card")
    def test_sucesso_contem_email_e_ticket(self, mock_card):
        """Deve incluir e-mail e ticket nos widgets."""
        mock_card.return_value = True

        notificar_sucesso("user@empresa.com", "SPN-300", "https://link", "Pedro")

        _, kwargs = mock_card.call_args
        widgets = kwargs["secoes"][0]["widgets"]
        textos = [w["decoratedText"]["text"] for w in widgets]
        assert "user@empresa.com" in textos
        assert "SPN-300" in textos


# ============================================================
# Testes de notificar_erro
# ============================================================

class TestNotificarErro:
    """Testes para a função notificar_erro."""

    @patch("servicos.google_chat._enviar_card")
    def test_erro_com_descricao(self, mock_card):
        """Deve incluir a descrição do erro no card."""
        mock_card.return_value = True

        notificar_erro("user@empresa.com", "SPN-100", "Timeout na exportação", "Lucas")

        _, kwargs = mock_card.call_args
        assert "Erro" in kwargs["titulo"]
        widgets = kwargs["secoes"][0]["widgets"]
        textos = [w["decoratedText"]["text"] for w in widgets]
        assert "Timeout na exportação" in textos

    @patch("servicos.google_chat._enviar_card")
    def test_erro_contem_acao_necessaria(self, mock_card):
        """Deve incluir orientação de ação necessária."""
        mock_card.return_value = True

        notificar_erro("user@empresa.com", "SPN-100", "Falha de rede")

        _, kwargs = mock_card.call_args
        widgets = kwargs["secoes"][0]["widgets"]
        labels = [w["decoratedText"]["topLabel"] for w in widgets]
        assert "Ação Necessária" in labels

    @patch("servicos.google_chat._enviar_card")
    def test_erro_sem_nome_usa_email(self, mock_card):
        """Deve usar e-mail no subtítulo quando nome não é informado."""
        mock_card.return_value = True

        notificar_erro("user@empresa.com", "SPN-100", "Erro X")

        _, kwargs = mock_card.call_args
        assert "user@empresa.com" in kwargs["subtitulo"]


# ============================================================
# Testes de notificar_conta_excluida
# ============================================================

class TestNotificarContaExcluida:
    """Testes para a função notificar_conta_excluida."""

    @patch("servicos.google_chat._enviar_card")
    def test_conta_excluida_com_nome(self, mock_card):
        """Deve indicar exclusão com nome do colaborador."""
        mock_card.return_value = True

        notificar_conta_excluida("user@empresa.com", "SPN-100", "Fernanda")

        _, kwargs = mock_card.call_args
        assert "Excluída" in kwargs["titulo"]
        assert "Fernanda" in kwargs["subtitulo"]

    @patch("servicos.google_chat._enviar_card")
    def test_conta_excluida_widgets(self, mock_card):
        """Deve incluir status de exclusão nos widgets."""
        mock_card.return_value = True

        notificar_conta_excluida("user@empresa.com", "SPN-100")

        _, kwargs = mock_card.call_args
        widgets = kwargs["secoes"][0]["widgets"]
        textos = [w["decoratedText"]["text"] for w in widgets]
        assert any("excluída" in t.lower() for t in textos)

    @patch("servicos.google_chat._enviar_card")
    def test_conta_excluida_sem_nome(self, mock_card):
        """Deve usar e-mail quando nome não é fornecido."""
        mock_card.return_value = True

        notificar_conta_excluida("user@empresa.com", "SPN-100")

        _, kwargs = mock_card.call_args
        assert "user@empresa.com" in kwargs["subtitulo"]


# ============================================================
# Testes de resiliência (falhas não devem propagar)
# ============================================================

class TestResiliencia:
    """Testes para garantir que falhas no Chat não quebram o fluxo."""

    @patch("servicos.google_chat.GOOGLE_CHAT_WEBHOOK_URL", _WEBHOOK_TESTE)
    @patch("servicos.google_chat.requests.post")
    def test_timeout_retorna_false(self, mock_post):
        """Timeout deve retornar False sem lançar exceção."""
        mock_post.side_effect = requests.exceptions.Timeout("Timeout")

        resultado = notificar_inicio("user@empresa.com", "SPN-100")

        assert resultado is False

    @patch("servicos.google_chat.GOOGLE_CHAT_WEBHOOK_URL", _WEBHOOK_TESTE)
    @patch("servicos.google_chat.requests.post")
    def test_erro_conexao_retorna_false(self, mock_post):
        """Erro de conexão deve retornar False sem lançar exceção."""
        mock_post.side_effect = requests.exceptions.ConnectionError("Sem rede")

        resultado = notificar_erro("user@empresa.com", "SPN-100", "Erro")

        assert resultado is False

    @patch("servicos.google_chat.GOOGLE_CHAT_WEBHOOK_URL", _WEBHOOK_TESTE)
    @patch("servicos.google_chat.requests.post")
    def test_erro_http_500_retorna_false(self, mock_post):
        """Erro HTTP 500 deve retornar False sem lançar exceção."""
        resposta_mock = MagicMock()
        resposta_mock.raise_for_status.side_effect = requests.exceptions.HTTPError("500")
        mock_post.return_value = resposta_mock

        resultado = notificar_sucesso("user@empresa.com", "SPN-100", "https://link")

        assert resultado is False
