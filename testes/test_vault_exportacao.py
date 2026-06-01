"""
Testes do módulo vault_exportacao.py

Verifica:
- Criação de exports de e-mail e Drive
- Reaproveitamento de exports existentes
- Retry com backoff em caso de falha
- Monitoramento de status
"""

import pytest
from unittest.mock import MagicMock, call


@pytest.fixture
def mock_vault(mocker):
    """Mock do serviço Vault com comportamento padrão."""
    mock = MagicMock()
    mocker.patch("servicos.vault_exportacao.obter_servico_vault", return_value=mock)
    return mock


@pytest.fixture(autouse=True)
def sem_sleep(mocker):
    """Remove sleeps para acelerar os testes."""
    mocker.patch("time.sleep")


class TestCriarExportacaoEmail:
    def test_cria_novo_export(self, mock_vault, mocker):
        # Simula lista vazia (sem exports existentes)
        mock_vault.matters().exports().list().execute.return_value = {"exports": []}
        mock_vault.matters().exports().create().execute.return_value = {
            "id": "export-123",
            "name": "Email_user@empresa.com_20260401_120000",
            "status": "IN_PROGRESS",
        }

        from servicos.vault_exportacao import criar_exportacao_email
        resultado = criar_exportacao_email("user@empresa.com")

        assert resultado["id"] == "export-123"
        assert resultado.get("_reaproveitado") is not True

    def test_reaproveita_export_existente(self, mock_vault):
        export_existente = {
            "id": "export-existente",
            "name": "Email_user@empresa.com_20260401_000000",
            "status": "COMPLETED",
        }
        mock_vault.matters().exports().list().execute.return_value = {
            "exports": [export_existente]
        }

        from servicos.vault_exportacao import criar_exportacao_email
        resultado = criar_exportacao_email("user@empresa.com")

        assert resultado["id"] == "export-existente"
        assert resultado["_reaproveitado"] is True
        # Não deve ter chamado .create()
        mock_vault.matters().exports().create.assert_not_called()


class TestMonitorarExportacao:
    def test_retorna_quando_completed(self, mock_vault):
        mock_vault.matters().exports().get().execute.return_value = {
            "id": "export-123",
            "name": "Email_user",
            "status": "COMPLETED",
        }

        from servicos.vault_exportacao import monitorar_exportacao
        # semaforo_adquirido=False para não precisar do semáforo real
        resultado = monitorar_exportacao("export-123", semaforo_adquirido=False)

        assert resultado["status"] == "COMPLETED"

    def test_levanta_excecao_quando_failed(self, mock_vault):
        mock_vault.matters().exports().get().execute.return_value = {
            "id": "export-123",
            "name": "Email_user",
            "status": "FAILED",
        }

        from servicos.vault_exportacao import monitorar_exportacao
        with pytest.raises(Exception, match="FALHOU"):
            monitorar_exportacao("export-123", semaforo_adquirido=False)


class TestBackoffExponencial:
    def test_calcular_backoff_progressao(self):
        from utils.retry import calcular_backoff
        assert calcular_backoff(1) == 30
        assert calcular_backoff(2) == 60
        assert calcular_backoff(3) == 120
        assert calcular_backoff(4) == 240
        assert calcular_backoff(5) == 300  # cap
        assert calcular_backoff(10) == 300  # ainda no cap

    def test_calcular_backoff_customizado(self):
        from utils.retry import calcular_backoff
        assert calcular_backoff(1, base=10, multiplicador=3, maximo=100) == 10
        assert calcular_backoff(2, base=10, multiplicador=3, maximo=100) == 30
        assert calcular_backoff(3, base=10, multiplicador=3, maximo=100) == 90
        assert calcular_backoff(4, base=10, multiplicador=3, maximo=100) == 100  # cap
