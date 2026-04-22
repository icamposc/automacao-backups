"""
Testes do módulo orquestrador.py

Verifica:
- Detecção de backup duplicado via banco
- Função esta_em_processamento
- Função executar_backup_direto (fluxo feliz mockado)
"""

import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def banco_isolado(banco_teste):
    yield


class TestEstaEmProcessamento:
    def test_retorna_false_sem_backup_ativo(self):
        from processamento.orquestrador import esta_em_processamento
        assert esta_em_processamento("ninguem@empresa.com") is False

    def test_retorna_true_com_backup_ativo(self):
        from processamento.rastreador import registrar_backup
        from processamento.orquestrador import esta_em_processamento

        registrar_backup("user@empresa.com", "SPN-1")
        assert esta_em_processamento("user@empresa.com") is True

    def test_retorna_false_apos_finalizar(self):
        from processamento.rastreador import registrar_backup, finalizar_backup
        from processamento.orquestrador import esta_em_processamento

        registrar_backup("user@empresa.com", "SPN-1")
        finalizar_backup("user@empresa.com", sucesso=True)
        assert esta_em_processamento("user@empresa.com") is False


class TestExcecoes:
    def test_erro_vault_timeout_e_subclasse_de_erro_backup(self):
        from utils.excecoes import ErroVaultTimeout, ErroBackup
        erro = ErroVaultTimeout("Timeout após 6.0 horas")
        assert isinstance(erro, ErroBackup)

    def test_erro_upload_e_subclasse_de_erro_backup(self):
        from utils.excecoes import ErroUpload, ErroBackup
        assert issubclass(ErroUpload, ErroBackup)

    def test_hierarquia_completa(self):
        from utils.excecoes import (
            ErroBackup, ErroVaultTimeout, ErroVaultFalha,
            ErroDownload, ErroUpload, ErroExclusaoConta, ErroJira,
        )
        for cls in [ErroVaultTimeout, ErroVaultFalha, ErroDownload,
                    ErroUpload, ErroExclusaoConta, ErroJira]:
            assert issubclass(cls, ErroBackup)


class TestIniciarBackupAsync:
    def test_enfileira_no_celery(self):
        # worker.tarefas já está pré-mockado no conftest.py
        import sys
        mock_tarefas = sys.modules["worker.tarefas"]
        mock_tarefas.executar_backup.delay.reset_mock()

        from processamento.orquestrador import iniciar_backup_async
        iniciar_backup_async("user@empresa.com", "SPN-1", "João")

        mock_tarefas.executar_backup.delay.assert_called_once_with(
            "user@empresa.com", "SPN-1", "João", True
        )
