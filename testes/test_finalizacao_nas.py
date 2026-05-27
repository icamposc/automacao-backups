"""
Testes do modulo processamento/finalizacao_nas.py

Verifica:
- Backup nao finaliza antes de 23h em aguardando_nas
- Backup finaliza apos 23h: status=concluido, comentar_sucesso, transicionar,
  chat de sucesso
- Quando deletar_conta=True chama servicos.conta_exclusao.deletar_conta
- Idempotencia: segunda execucao com mesmos pendentes vira no-op
- Falha em um backup nao impede processamento dos outros
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture(autouse=True)
def banco_isolado(banco_teste):
    yield


def _criar_backup_aguardando_nas(
    email: str,
    ticket_id: str,
    caminho_zip: str,
    horas_atras: int,
    deletar_conta: bool = False,
):
    """Insere backup em aguardando_nas com inicio_aguardando_nas no passado.

    Atalho que evita ter de simular o orquestrador inteiro. Usa o repositorio
    para inserir + marcar e ajusta o inicio_aguardando_nas direto no banco.
    """
    from dados.banco import obter_conexao
    from dados.repositorio_backups import inserir_backup, marcar_aguardando_nas

    inserir_backup(email, ticket_id, "Teste", deletar_conta=deletar_conta)
    marcar_aguardando_nas(email, caminho_zip)

    # Backdate inicio_aguardando_nas
    ts = (datetime.now(timezone.utc) - timedelta(hours=horas_atras)).isoformat()
    conn = obter_conexao()
    conn.execute(
        "UPDATE backups SET inicio_aguardando_nas = ? WHERE email = ?",
        (ts, email),
    )
    conn.commit()


def _patches_jira_chat_conta():
    """Context manager combinado dos mocks usados em quase todos os testes."""
    return {
        "comentar_sucesso": patch("servicos.jira_atualizacao.comentar_sucesso"),
        "transicionar_resolvido": patch("servicos.jira_atualizacao.transicionar_resolvido"),
        "deletar_conta": patch("servicos.conta_exclusao.deletar_conta"),
        "chat_sucesso": patch("servicos.google_chat.notificar_sucesso"),
        "chat_erro_conta": patch("servicos.google_chat.notificar_erro_exclusao_conta"),
    }


class TestFinalizacaoNas:
    def test_nao_finaliza_antes_de_23h(self, tmp_path):
        from processamento.finalizacao_nas import finalizar_backups_pendentes

        # Apenas 1h em aguardando_nas — abaixo de 23h
        _criar_backup_aguardando_nas(
            email="cedo@empresa.com",
            ticket_id="TICK-1",
            caminho_zip=f"nas:{tmp_path}/cedo@empresa.com_X.zip",
            horas_atras=1,
        )

        ctx = _patches_jira_chat_conta()
        with ctx["comentar_sucesso"] as m_jira, \
             ctx["transicionar_resolvido"] as m_trans, \
             ctx["chat_sucesso"] as m_chat:
            r = finalizar_backups_pendentes()

        assert r == {"finalizados": 0}
        m_jira.assert_not_called()
        m_trans.assert_not_called()
        m_chat.assert_not_called()

    def test_finaliza_apos_23h(self, tmp_path):
        from processamento.finalizacao_nas import finalizar_backups_pendentes
        from dados.banco import obter_conexao

        zip_path = tmp_path / "tarde@empresa.com_X.zip"
        zip_path.write_bytes(b"x" * 100)
        link = f"nas:{zip_path}"
        _criar_backup_aguardando_nas(
            email="tarde@empresa.com",
            ticket_id="TICK-2",
            caminho_zip=link,
            horas_atras=24,
            deletar_conta=False,
        )

        ctx = _patches_jira_chat_conta()
        with ctx["comentar_sucesso"] as m_jira, \
             ctx["transicionar_resolvido"] as m_trans, \
             ctx["deletar_conta"] as m_del, \
             ctx["chat_sucesso"] as m_chat:
            r = finalizar_backups_pendentes()

        assert r == {"finalizados": 1}
        m_jira.assert_called_once()
        m_trans.assert_called_once_with("TICK-2")
        m_chat.assert_called_once()
        m_del.assert_not_called()  # deletar_conta=False

        # DB: status virou concluido
        row = obter_conexao().execute(
            "SELECT status_geral, fim, link_drive FROM backups WHERE email = ?",
            ("tarde@empresa.com",),
        ).fetchone()
        assert row["status_geral"] == "concluido"
        assert row["fim"] is not None
        assert row["link_drive"] == link

    def test_finaliza_com_delete_conta(self, tmp_path):
        from processamento.finalizacao_nas import finalizar_backups_pendentes

        zip_path = tmp_path / "del@empresa.com_X.zip"
        zip_path.write_bytes(b"x")
        _criar_backup_aguardando_nas(
            email="del@empresa.com",
            ticket_id="TICK-DEL",
            caminho_zip=f"nas:{zip_path}",
            horas_atras=24,
            deletar_conta=True,
        )

        ctx = _patches_jira_chat_conta()
        with ctx["comentar_sucesso"], \
             ctx["transicionar_resolvido"], \
             ctx["deletar_conta"] as m_del, \
             ctx["chat_sucesso"]:
            finalizar_backups_pendentes()

        m_del.assert_called_once_with("del@empresa.com")

    def test_apaga_zip_local_na_finalizacao(self, tmp_path):
        from processamento.finalizacao_nas import finalizar_backups_pendentes

        zip_path = tmp_path / "apaga@empresa.com_Z.zip"
        zip_path.write_bytes(b"z" * 100)

        _criar_backup_aguardando_nas(
            email="apaga@empresa.com",
            ticket_id="TICK-ZIP",
            caminho_zip=f"nas:{zip_path}",
            horas_atras=7,  # acima da janela de 6h
        )

        ctx = _patches_jira_chat_conta()
        with ctx["comentar_sucesso"], \
             ctx["transicionar_resolvido"], \
             ctx["deletar_conta"], \
             ctx["chat_sucesso"]:
            finalizar_backups_pendentes()

        assert not zip_path.exists(), "ZIP local deveria ter sido apagado na finalizacao"

    def test_nao_apaga_zip_no_fallback_drive(self, tmp_path):
        """Quando o destino foi o Drive (link https), nada e apagado do disco."""
        from processamento.finalizacao_nas import _apagar_zip_local

        zip_path = tmp_path / "drive@empresa.com.zip"
        zip_path.write_bytes(b"x")

        # link de fallback Drive nao comeca com 'nas:' -> no-op
        _apagar_zip_local("https://drive.google.com/file/d/abc")

        assert zip_path.exists(), "ZIP nao deveria ser tocado no fluxo Drive"

    def test_idempotente_segunda_execucao(self, tmp_path):
        from processamento.finalizacao_nas import finalizar_backups_pendentes

        zip_path = tmp_path / "idem@empresa.com_X.zip"
        zip_path.write_bytes(b"x")
        _criar_backup_aguardando_nas(
            email="idem@empresa.com",
            ticket_id="TICK-IDEM",
            caminho_zip=f"nas:{zip_path}",
            horas_atras=24,
        )

        ctx = _patches_jira_chat_conta()
        with ctx["comentar_sucesso"] as m_jira, \
             ctx["transicionar_resolvido"], \
             ctx["deletar_conta"], \
             ctx["chat_sucesso"]:
            r1 = finalizar_backups_pendentes()
            r2 = finalizar_backups_pendentes()

        assert r1 == {"finalizados": 1}
        assert r2 == {"finalizados": 0}  # segundo run nao acha mais o backup
        assert m_jira.call_count == 1

    def test_falha_jira_nao_quebra_outros(self, tmp_path):
        from processamento.finalizacao_nas import finalizar_backups_pendentes
        from dados.banco import obter_conexao

        # Dois backups prontos. O primeiro vai falhar no transicionar_resolvido,
        # o segundo deve mesmo assim processar (status DB=concluido).
        zip1 = tmp_path / "falha@empresa.com_X.zip"; zip1.write_bytes(b"a")
        zip2 = tmp_path / "sucesso@empresa.com_X.zip"; zip2.write_bytes(b"b")
        _criar_backup_aguardando_nas("falha@empresa.com",   "TF-1", f"nas:{zip1}", 24)
        _criar_backup_aguardando_nas("sucesso@empresa.com", "TF-2", f"nas:{zip2}", 24)

        ctx = _patches_jira_chat_conta()
        with ctx["comentar_sucesso"], \
             ctx["deletar_conta"], \
             ctx["chat_sucesso"], \
             patch("servicos.jira_atualizacao.transicionar_resolvido",
                   side_effect=[Exception("boom"), None]):
            r = finalizar_backups_pendentes()

        # Ambos contam como finalizados (excecao em jira nao reverte o DB).
        assert r == {"finalizados": 2}

        # Ambos com status=concluido no DB
        rows = obter_conexao().execute(
            "SELECT email, status_geral FROM backups WHERE email IN (?, ?)",
            ("falha@empresa.com", "sucesso@empresa.com"),
        ).fetchall()
        assert all(r["status_geral"] == "concluido" for r in rows)


class TestAuxiliares:
    def test_listar_prontos_para_finalizar_filtra_status(self, tmp_path):
        """Confirma que listar_prontos_para_finalizar so retorna aguardando_nas
        com inicio_aguardando_nas > 23h."""
        from dados.repositorio_backups import (
            inserir_backup, finalizar_backup, listar_prontos_para_finalizar,
        )

        # 1 em_andamento (nao deve retornar)
        inserir_backup("ativo@empresa.com", "T-A", "X")

        # 1 concluido (nao deve retornar)
        inserir_backup("done@empresa.com", "T-B", "X")
        finalizar_backup("done@empresa.com", sucesso=True)

        # 1 aguardando_nas com 24h (deve retornar)
        _criar_backup_aguardando_nas("pronto@empresa.com", "T-P", "nas:/x.zip", 24)

        # 1 aguardando_nas com 1h (NAO deve retornar)
        _criar_backup_aguardando_nas("recente@empresa.com", "T-R", "nas:/y.zip", 1)

        pendentes = listar_prontos_para_finalizar(horas=23)
        emails = {p["email"] for p in pendentes}
        assert emails == {"pronto@empresa.com"}

    def test_marcar_aguardando_nas_atualiza_status_e_timestamp(self):
        from dados.banco import obter_conexao
        from dados.repositorio_backups import inserir_backup, marcar_aguardando_nas

        inserir_backup("x@e.com", "T-X", "Nome")
        marcar_aguardando_nas("x@e.com", "nas:/path.zip")

        row = obter_conexao().execute(
            "SELECT status_geral, link_drive, inicio_aguardando_nas FROM backups WHERE email=?",
            ("x@e.com",),
        ).fetchone()
        assert row["status_geral"] == "aguardando_nas"
        assert row["link_drive"] == "nas:/path.zip"
        assert row["inicio_aguardando_nas"] is not None
