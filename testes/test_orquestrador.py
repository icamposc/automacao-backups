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
            ErroEspacoInsuficiente, ErroRecuperacaoBloqueada,
        )
        for cls in [ErroVaultTimeout, ErroVaultFalha, ErroDownload,
                    ErroUpload, ErroExclusaoConta, ErroJira,
                    ErroEspacoInsuficiente, ErroRecuperacaoBloqueada]:
            assert issubclass(cls, ErroBackup)

    def test_erro_espaco_insuficiente_carrega_metricas(self):
        from utils.excecoes import ErroEspacoInsuficiente
        erro = ErroEspacoInsuficiente("nao cabe", necessario_gb=1700.0, disponivel_gb=400.0)
        assert erro.necessario_gb == 1700.0
        assert erro.disponivel_gb == 400.0


class TestVerificarCapacidadeDisco:
    """Pre-flight: aborta antes do download se backup não couber."""

    def _stats(self, size_bytes: int) -> dict:
        return {"stats": {"sizeInBytes": size_bytes}}

    def _disk_usage(self, free_bytes: int):
        from collections import namedtuple
        Usage = namedtuple("Usage", "total used free")
        return Usage(total=free_bytes * 2, used=free_bytes, free=free_bytes)

    def test_passa_quando_cabe_com_margem_folgada(self, tmp_path):
        from processamento.orquestrador import _verificar_capacidade_disco

        export_email = self._stats(10 * 1024 ** 3)  # 10 GB
        export_drive = self._stats(20 * 1024 ** 3)  # 20 GB
        # 100 GB livres — 80 GB utilizáveis; 30 GB necessários => OK.
        with patch("processamento.orquestrador.shutil.disk_usage",
                   return_value=self._disk_usage(100 * 1024 ** 3)):
            _verificar_capacidade_disco(export_email, export_drive, tmp_path)

    def test_levanta_excecao_quando_nao_cabe(self, tmp_path):
        from processamento.orquestrador import _verificar_capacidade_disco
        from utils.excecoes import ErroEspacoInsuficiente

        # Cenário David Ortiz: 1.77 TB necessário, 400 GB livres.
        export_email = self._stats(97 * 1024 ** 3)
        export_drive = self._stats(1668 * 1024 ** 3)
        with patch("processamento.orquestrador.shutil.disk_usage",
                   return_value=self._disk_usage(400 * 1024 ** 3)):
            with pytest.raises(ErroEspacoInsuficiente) as ex:
                _verificar_capacidade_disco(export_email, export_drive, tmp_path)

        assert ex.value.necessario_gb > 1000
        assert ex.value.disponivel_gb == pytest.approx(400.0, rel=0.01)

    def test_levanta_no_limite_da_margem(self, tmp_path):
        """Necessário = 81% do disco → bloqueia (margem é 20%)."""
        from processamento.orquestrador import _verificar_capacidade_disco
        from utils.excecoes import ErroEspacoInsuficiente

        export_email = self._stats(0)
        export_drive = self._stats(int(100 * 1024 ** 3 * 0.81))  # 81 GB de 100 livres
        with patch("processamento.orquestrador.shutil.disk_usage",
                   return_value=self._disk_usage(100 * 1024 ** 3)):
            with pytest.raises(ErroEspacoInsuficiente):
                _verificar_capacidade_disco(export_email, export_drive, tmp_path)

    def test_segue_quando_size_inconclusivo(self, tmp_path):
        """Se Vault não reportou sizeInBytes ainda, não bloqueia."""
        from processamento.orquestrador import _verificar_capacidade_disco

        with patch("processamento.orquestrador.shutil.disk_usage",
                   return_value=self._disk_usage(10 * 1024 ** 3)):
            # Ambos sem stats — deve passar sem exceção (pre-flight inconclusivo).
            _verificar_capacidade_disco({}, {}, tmp_path)


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
