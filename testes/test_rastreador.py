"""
Testes do módulo rastreador.py (com SQLite)

Verifica:
- Registro de backups
- Atualização de etapas
- Finalização com sucesso/erro
- Consultas de ativos, histórico e resumo
- Persistência entre chamadas
"""

import pytest


@pytest.fixture(autouse=True)
def banco_isolado(banco_teste):
    """Garante banco isolado para cada teste."""
    yield


class TestRegistrarBackup:
    def test_registra_backup_com_8_etapas(self):
        from processamento.rastreador import registrar_backup, obter_backup
        registrar_backup("user@empresa.com", "SPN-1", "Usuário Teste")

        backup = obter_backup("user@empresa.com")
        assert backup is not None
        assert backup["email"] == "user@empresa.com"
        assert backup["ticket_id"] == "SPN-1"
        assert backup["nome"] == "Usuário Teste"
        assert backup["status_geral"] == "em_andamento"
        assert len(backup["etapas"]) == 8

    def test_todas_etapas_iniciam_como_pendente(self):
        from processamento.rastreador import registrar_backup, obter_backup
        registrar_backup("user@empresa.com", "SPN-1")

        backup = obter_backup("user@empresa.com")
        for etapa in backup["etapas"]:
            assert etapa["status"] == "pendente"


class TestAtualizarEtapa:
    def test_atualiza_status_para_em_andamento(self):
        from processamento.rastreador import registrar_backup, atualizar_etapa, obter_backup, STATUS_EM_ANDAMENTO
        registrar_backup("user@empresa.com", "SPN-1")
        atualizar_etapa("user@empresa.com", 1, STATUS_EM_ANDAMENTO)

        backup = obter_backup("user@empresa.com")
        etapa1 = next(e for e in backup["etapas"] if e["numero"] == 1)
        assert etapa1["status"] == "em_andamento"
        assert etapa1["inicio"] is not None

    def test_atualiza_status_para_concluido(self):
        from processamento.rastreador import registrar_backup, atualizar_etapa, obter_backup, STATUS_CONCLUIDO, STATUS_EM_ANDAMENTO
        registrar_backup("user@empresa.com", "SPN-1")
        atualizar_etapa("user@empresa.com", 1, STATUS_EM_ANDAMENTO)
        atualizar_etapa("user@empresa.com", 1, STATUS_CONCLUIDO)

        backup = obter_backup("user@empresa.com")
        etapa1 = next(e for e in backup["etapas"] if e["numero"] == 1)
        assert etapa1["status"] == "concluido"
        assert etapa1["fim"] is not None


class TestFinalizarBackup:
    def test_finalizar_com_sucesso(self):
        from processamento.rastreador import registrar_backup, finalizar_backup, obter_backup
        registrar_backup("user@empresa.com", "SPN-1")
        finalizar_backup("user@empresa.com", sucesso=True, link_drive="https://drive.google.com/...")

        backup = obter_backup("user@empresa.com")
        assert backup["status_geral"] == "concluido"
        assert backup["link_drive"] == "https://drive.google.com/..."
        assert backup["fim"] is not None

    def test_finalizar_com_erro(self):
        from processamento.rastreador import registrar_backup, finalizar_backup, obter_backup
        registrar_backup("user@empresa.com", "SPN-1")
        finalizar_backup("user@empresa.com", sucesso=False, erro_mensagem="Timeout no Vault")

        backup = obter_backup("user@empresa.com")
        assert backup["status_geral"] == "erro"
        assert backup["erro_mensagem"] == "Timeout no Vault"

    def test_finalizado_vai_para_historico(self):
        from processamento.rastreador import registrar_backup, finalizar_backup, obter_historico, obter_backups_ativos
        registrar_backup("user@empresa.com", "SPN-1")
        finalizar_backup("user@empresa.com", sucesso=True)

        assert len(obter_backups_ativos()) == 0
        historico = obter_historico()
        assert len(historico) == 1


class TestResumo:
    def test_resumo_vazio(self):
        from processamento.rastreador import obter_resumo
        resumo = obter_resumo()
        assert resumo["ativos"] == 0
        assert resumo["total_finalizados"] == 0

    def test_resumo_com_backups(self):
        from processamento.rastreador import registrar_backup, finalizar_backup, obter_resumo
        registrar_backup("u1@empresa.com", "SPN-1")
        registrar_backup("u2@empresa.com", "SPN-2")
        finalizar_backup("u1@empresa.com", sucesso=True)
        finalizar_backup("u2@empresa.com", sucesso=False)

        resumo = obter_resumo()
        assert resumo["ativos"] == 0
        assert resumo["sucessos"] == 1
        assert resumo["erros"] == 1
