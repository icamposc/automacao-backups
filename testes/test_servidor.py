"""
Testes do servidor Flask — /health e /saude

Verifica:
- /health retorna 200 quando tudo OK
- /health retorna 503 quando algum componente está degradado
- /health detecta Celery sem workers (caso D-state)
- /health detecta disco abaixo do threshold
- /health detecta backups stuck (em_andamento há > 12h)
- /saude tem o mesmo comportamento de /health (alias legado)
"""

from collections import namedtuple
from datetime import datetime, timedelta, timezone

import pytest
from unittest.mock import MagicMock


_DiskUsage = namedtuple("DiskUsage", "total used free")


@pytest.fixture
def healthchecks_ok(mocker):
    """Mock de todos os componentes externos como saudáveis."""
    mocker.patch("redis.from_url", return_value=MagicMock(ping=MagicMock(return_value=True)))
    # Disco com 80% livre
    mocker.patch(
        "shutil.disk_usage",
        return_value=_DiskUsage(total=400 * 1024 ** 3, used=80 * 1024 ** 3, free=320 * 1024 ** 3),
    )
    # Celery responde com 1 worker
    mock_celery = MagicMock()
    mock_celery.control.inspect.return_value.ping.return_value = {"worker@host": {"ok": "pong"}}
    mocker.patch("worker.celery_app.app", mock_celery)
    return {"celery": mock_celery}


@pytest.fixture
def cliente_admin(cliente_flask):
    """
    Cliente de teste com sessão de admin já autenticada.

    O payload detalhado de /health e /saude (componentes, contadores, PII)
    só é exposto para usuários logados — sem sessão, a resposta é mínima.
    """
    with cliente_flask.session_transaction() as sess:
        sess["usuario"] = "admin.teste"
        sess["nome"] = "Admin Teste"
    return cliente_flask


class TestHealthCheck:
    """Corpo DETALHADO — exige sessão de admin (cliente_admin)."""

    def test_health_ok_quando_tudo_saudavel(self, cliente_admin, healthchecks_ok):
        resp = cliente_admin.get("/health")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "ok"
        assert body["componentes"]["banco"] == "ok"
        assert body["componentes"]["redis"] == "ok"
        assert "ok" in body["componentes"]["celery"]
        assert body["componentes"]["disco"] == "ok"
        assert body["backups_stuck"] == []

    def test_health_503_quando_celery_sem_workers(self, cliente_admin, healthchecks_ok):
        """Worker em D-state: Redis up, mas inspect ping nao retorna."""
        healthchecks_ok["celery"].control.inspect.return_value.ping.return_value = None

        resp = cliente_admin.get("/health")
        assert resp.status_code == 503
        body = resp.get_json()
        assert body["status"] == "degradado"
        assert body["componentes"]["celery"] == "sem_workers"

    def test_health_503_quando_disco_abaixo_do_threshold(self, cliente_admin, healthchecks_ok, mocker):
        # 5% livre — abaixo do limite de 10%
        mocker.patch(
            "shutil.disk_usage",
            return_value=_DiskUsage(total=400 * 1024 ** 3, used=380 * 1024 ** 3, free=20 * 1024 ** 3),
        )
        resp = cliente_admin.get("/health")
        assert resp.status_code == 503
        body = resp.get_json()
        assert "degradado" in body["componentes"]["disco"]
        assert body["componentes"]["disco_detalhe"]["livre_pct"] == pytest.approx(5.0, abs=0.5)

    def test_health_inclui_disco_detalhe(self, cliente_admin, healthchecks_ok):
        resp = cliente_admin.get("/health")
        body = resp.get_json()
        detalhe = body["componentes"]["disco_detalhe"]
        assert "total_gb" in detalhe
        assert "livre_gb" in detalhe
        assert "livre_pct" in detalhe
        assert detalhe["livre_gb"] > 0

    def test_health_503_quando_backup_stuck(self, cliente_admin, healthchecks_ok):
        """Backup em_andamento há > 12h dispara degradado."""
        from dados.banco import obter_conexao

        # Insere um backup com inicio há 24h
        antigo = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        conn = obter_conexao()
        conn.execute(
            """INSERT INTO backups (email, ticket_id, nome, status_geral, inicio, deletar_conta)
               VALUES (?, ?, ?, 'em_andamento', ?, 1)""",
            ("stuck@x.com", "SPN-STUCK", "Stuck", antigo),
        )
        conn.commit()

        resp = cliente_admin.get("/health")
        assert resp.status_code == 503
        body = resp.get_json()
        assert len(body["backups_stuck"]) == 1
        assert body["backups_stuck"][0]["email"] == "stuck@x.com"
        assert body["backups_stuck"][0]["idade_horas"] >= 12

    def test_health_nao_lista_backup_recente_como_stuck(self, cliente_admin, healthchecks_ok):
        from dados.banco import obter_conexao

        recente = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        conn = obter_conexao()
        conn.execute(
            """INSERT INTO backups (email, ticket_id, nome, status_geral, inicio, deletar_conta)
               VALUES (?, ?, ?, 'em_andamento', ?, 1)""",
            ("recente@x.com", "SPN-OK", "Recente", recente),
        )
        conn.commit()

        resp = cliente_admin.get("/health")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["backups_stuck"] == []


class TestHealthPublico:
    """Corpo PÚBLICO (sem sessão) — mínimo, sem componentes nem PII."""

    def test_health_publico_e_minimo_e_sem_pii(self, cliente_flask, healthchecks_ok):
        resp = cliente_flask.get("/health")
        assert resp.status_code == 200
        body = resp.get_json()
        # Só o essencial
        assert body["status"] == "ok"
        assert "timestamp" in body
        # NÃO expõe diagnóstico nem dados sensíveis sem login
        for campo in ("componentes", "backups_stuck", "ultima_execucao",
                      "resumo", "backups_em_andamento"):
            assert campo not in body

    def test_health_publico_preserva_codigo_503(self, cliente_flask, healthchecks_ok):
        # Docker depende do código HTTP, não do corpo.
        healthchecks_ok["celery"].control.inspect.return_value.ping.return_value = None
        resp = cliente_flask.get("/health")
        assert resp.status_code == 503
        body = resp.get_json()
        assert body["status"] == "degradado"
        assert "componentes" not in body


class TestSaudeAlias:
    def test_saude_delega_para_health(self, cliente_admin, healthchecks_ok):
        """/saude deve retornar o mesmo conteúdo e status que /health."""
        resp_saude = cliente_admin.get("/saude")
        resp_health = cliente_admin.get("/health")

        assert resp_saude.status_code == resp_health.status_code
        body_saude = resp_saude.get_json()
        body_health = resp_health.get_json()
        # Removemos timestamp (varia entre chamadas)
        body_saude.pop("timestamp", None)
        body_health.pop("timestamp", None)
        assert body_saude == body_health

    def test_saude_retorna_503_quando_health_503(self, cliente_flask, healthchecks_ok):
        healthchecks_ok["celery"].control.inspect.return_value.ping.return_value = None
        resp = cliente_flask.get("/saude")
        assert resp.status_code == 503


class TestListarBackupsStuck:
    def test_retorna_vazio_sem_em_andamento(self, banco_teste):
        from dados.repositorio_backups import listar_backups_stuck
        assert listar_backups_stuck() == []

    def test_calcula_idade_em_horas(self, banco_teste):
        from dados.banco import obter_conexao
        from dados.repositorio_backups import listar_backups_stuck

        inicio = (datetime.now(timezone.utc) - timedelta(hours=15)).isoformat()
        conn = obter_conexao()
        conn.execute(
            """INSERT INTO backups (email, ticket_id, nome, status_geral, inicio, deletar_conta)
               VALUES (?, ?, ?, 'em_andamento', ?, 1)""",
            ("velho@x.com", "SPN-V", "Velho", inicio),
        )
        conn.commit()

        stuck = listar_backups_stuck(horas=12)
        assert len(stuck) == 1
        assert stuck[0]["email"] == "velho@x.com"
        assert 14.5 <= stuck[0]["idade_horas"] <= 15.5

    def test_ignora_backups_concluidos(self, banco_teste):
        from dados.repositorio_backups import (
            inserir_backup, finalizar_backup, listar_backups_stuck,
        )

        inserir_backup("ok@x.com", "SPN-OK", "OK")
        finalizar_backup("ok@x.com", sucesso=True, link_drive="link")

        assert listar_backups_stuck() == []

    def test_respeita_parametro_horas(self, banco_teste):
        """Backup de 6h é stuck para `horas=4` mas não para `horas=12`."""
        from dados.banco import obter_conexao
        from dados.repositorio_backups import listar_backups_stuck

        inicio = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
        conn = obter_conexao()
        conn.execute(
            """INSERT INTO backups (email, ticket_id, nome, status_geral, inicio, deletar_conta)
               VALUES (?, ?, ?, 'em_andamento', ?, 1)""",
            ("seis@x.com", "SPN-6", "Seis", inicio),
        )
        conn.commit()

        assert len(listar_backups_stuck(horas=4)) == 1
        assert listar_backups_stuck(horas=12) == []
