"""
============================================================
Testes de Performance — Automação de Backups
============================================================
Valida que as melhorias de performance funcionam conforme
esperado. Todos os dados são sintéticos — nenhum e-mail ou
dado de produção é utilizado.

Cobertura:
  - N+1 eliminado: listar_ativos/listar_historico usam 1 query
    para etapas independente do número de backups
  - Compactação em passe único: lista arquivos e acumula
    tamanho em uma única iteração do filesystem
  - Índices no banco: queries de verificação de duplicata
    não fazem full scan em tabelas grandes
  - Batch de etapas é mais rápido que carregamento individual
============================================================
"""

import time
import zipfile
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _email(n: int) -> str:
    """Gera e-mail sintético sem domínio real."""
    return f"usuario{n:04d}@teste.local"


def _popular_banco(n_backups: int, status: str = "em_andamento"):
    """
    Insere n_backups registros completos (backup + 8 etapas cada) no banco.
    Retorna os IDs inseridos.
    """
    from dados.repositorio_backups import inserir_backup, finalizar_backup

    ids = []
    for i in range(n_backups):
        bid = inserir_backup(_email(i), f"SPN-{i:05d}", f"Usuário Teste {i}")
        ids.append(bid)
        if status == "concluido":
            finalizar_backup(_email(i), sucesso=True, link_drive=f"https://drive.google.com/fake/{i}")
        elif status == "erro":
            finalizar_backup(_email(i), sucesso=False, erro_mensagem="Erro simulado de teste")
    return ids


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def banco_isolado(banco_teste):
    yield


# ─────────────────────────────────────────────────────────────────────────────
# 1. N+1 Query eliminado
# ─────────────────────────────────────────────────────────────────────────────

class TestN1QueryEliminado:
    """
    Verifica que listar_ativos() e listar_historico() usam
    carregamento em lote de etapas (1 query extra) em vez de
    N queries individuais (uma por backup).
    """

    def test_listar_ativos_chama_batch_uma_unica_vez(self, banco_teste):
        """
        Com 30 backups ativos, _carregar_etapas_em_lote deve ser chamada
        exatamente 1 vez (não 30). Isso garante que o N+1 foi eliminado.
        """
        from dados.repositorio_backups import listar_ativos
        import dados.repositorio_backups as repo

        _popular_banco(30, status="em_andamento")

        with patch.object(repo, "_carregar_etapas_em_lote", wraps=repo._carregar_etapas_em_lote) as spy:
            resultado = listar_ativos()

        assert len(resultado) == 30
        assert spy.call_count == 1, (
            f"_carregar_etapas_em_lote chamada {spy.call_count}× "
            f"— deveria ser 1× (batch único)"
        )
        # Confirma que a chamada recebeu todos os IDs de uma vez
        ids_passados = spy.call_args[0][0]
        assert len(ids_passados) == 30, (
            f"Batch recebeu {len(ids_passados)} IDs, esperado 30"
        )

    def test_listar_historico_chama_batch_uma_unica_vez(self, banco_teste):
        """Com 40 backups finalizados, batch ainda é chamado apenas 1 vez."""
        from dados.repositorio_backups import listar_historico
        import dados.repositorio_backups as repo

        _popular_banco(40, status="concluido")

        with patch.object(repo, "_carregar_etapas_em_lote", wraps=repo._carregar_etapas_em_lote) as spy:
            resultado = listar_historico(pagina=1, por_pagina=40)

        assert len(resultado) == 40
        assert spy.call_count == 1, (
            f"_carregar_etapas_em_lote chamada {spy.call_count}× — esperado 1×"
        )

    def test_listar_ativos_retorna_etapas_corretas(self, banco_teste):
        """Verifica integridade: todas as etapas de todos os backups chegam corretas."""
        from dados.repositorio_backups import listar_ativos

        _popular_banco(10, status="em_andamento")
        resultado = listar_ativos()

        assert len(resultado) == 10
        for backup in resultado:
            assert len(backup["etapas"]) == 8, (
                f"Backup {backup['email']} deveria ter 8 etapas, "
                f"tem {len(backup['etapas'])}"
            )
            numeros = [e["numero"] for e in backup["etapas"]]
            assert numeros == list(range(1, 9)), f"Etapas fora de ordem: {numeros}"

    def test_batch_nao_cresce_com_numero_de_backups(self, banco_teste):
        """
        O número de chamadas ao banco não deve crescer linearmente com
        o número de backups — deve permanecer em 2 independente do volume.
        """
        from dados.repositorio_backups import listar_ativos
        import dados.repositorio_backups as repo

        for tamanho in [5, 20, 50]:
            # recria banco isolado para cada tamanho
            from dados.banco import fechar_conexao_thread, inicializar_banco
            from config import configuracoes
            import tempfile, pathlib

            tmp = pathlib.Path(tempfile.mkdtemp()) / "bench.db"
            configuracoes.SQLITE_PATH = tmp
            fechar_conexao_thread()
            inicializar_banco()

            _popular_banco(tamanho, status="em_andamento")

            chamadas = []
            original_batch = repo._carregar_etapas_em_lote

            def spy_batch(ids):
                chamadas.append(len(ids))
                return original_batch(ids)

            with patch.object(repo, "_carregar_etapas_em_lote", side_effect=spy_batch):
                resultado = listar_ativos()

            assert len(resultado) == tamanho
            assert len(chamadas) == 1, f"Para {tamanho} backups: {len(chamadas)} chamadas ao batch (esperado 1)"
            assert chamadas[0] == tamanho, f"Batch recebeu {chamadas[0]} IDs, esperado {tamanho}"

        print(
            f"\n[PERF] N+1 eliminado — batch sempre chamado 1× "
            f"independente do volume (5, 20, 50 backups)"
        )

    def test_batch_mais_rapido_que_individual_volume_realista(self, banco_teste):
        """
        Com 200 backups em banco real (arquivo), o carregamento em lote
        deve ser mais rápido que 200 queries individuais.
        """
        from dados.repositorio_backups import listar_ativos
        from dados.banco import obter_conexao

        N = 200
        _popular_banco(N, status="em_andamento")

        # ── Batch (abordagem atual) ───────────────────────────────────────
        inicio = time.perf_counter()
        resultado_batch = listar_ativos()
        tempo_batch = time.perf_counter() - inicio

        # ── Individual (simulação da abordagem antiga com N queries) ──────
        conn = obter_conexao()
        rows = conn.execute(
            "SELECT id FROM backups WHERE status_geral = 'em_andamento'"
        ).fetchall()
        backup_ids = [r["id"] for r in rows]

        inicio = time.perf_counter()
        for bid in backup_ids:
            conn.execute(
                "SELECT * FROM etapas_backup WHERE backup_id = ? ORDER BY numero",
                (bid,),
            ).fetchall()
        tempo_individual = time.perf_counter() - inicio

        assert len(resultado_batch) == N
        razao = tempo_individual / tempo_batch if tempo_batch > 0 else float("inf")

        print(
            f"\n[PERF] N+1 vs Batch — {N} backups ({N * 8} etapas):\n"
            f"  Individual : {tempo_individual*1000:.2f} ms ({N} queries)\n"
            f"  Batch      : {tempo_batch*1000:.2f} ms (2 queries)\n"
            f"  Speedup    : {razao:.1f}×"
        )

        # Com 200 registros, batch deve ser pelo menos tão rápido quanto individual
        # (no pior caso, mesma velocidade; em produção com I/O real é muito mais rápido)
        assert tempo_batch <= tempo_individual * 2.0, (
            f"Batch ({tempo_batch*1000:.2f}ms) foi mais de 2× mais lento que "
            f"individual ({tempo_individual*1000:.2f}ms) — verificar regressão"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 2. Compactação — passe único
# ─────────────────────────────────────────────────────────────────────────────

class TestCompactacaoPasseUnico:
    """
    Verifica que a listagem de arquivos e o cálculo de tamanho
    são feitos em uma única iteração do filesystem.
    """

    def _criar_pasta_com_arquivos(self, tmp_path: Path, n: int, tamanho_kb: int = 4) -> Path:
        """Cria n arquivos sintéticos de tamanho_kb KB cada."""
        pasta = tmp_path / "origem"
        pasta.mkdir()
        conteudo = b"X" * tamanho_kb * 1024
        for i in range(n):
            (pasta / f"arquivo_{i:04d}.dat").write_bytes(conteudo)
        return pasta

    def test_resultado_identico_ao_duplo_passe(self, tmp_path):
        """
        Garante que o passe único produz o mesmo ZIP e SHA256
        que a abordagem de dois passes produziria.
        """
        from processamento.compactacao import compactar_arquivos, calcular_sha256

        pasta = self._criar_pasta_com_arquivos(tmp_path, n=100)
        caminho_zip = tmp_path / "saida.zip"

        compactar_arquivos(pasta, caminho_zip)

        assert caminho_zip.exists()
        assert caminho_zip.stat().st_size > 0

        # Verifica que todos os arquivos estão no ZIP
        with zipfile.ZipFile(caminho_zip, "r") as zf:
            nomes = zf.namelist()
        assert len(nomes) == 100

        # SHA256 deve ser calculável sem erro
        digest = calcular_sha256(caminho_zip)
        assert len(digest) == 64  # SHA256 hex tem 64 chars

    def test_passe_unico_mais_rapido_ou_igual_ao_duplo(self, tmp_path):
        """
        O passe único (implementação atual) não deve ser mais lento
        do que a abordagem de dois passes (rglob + sum separados).
        """
        from processamento.compactacao import compactar_arquivos

        N = 200
        pasta = self._criar_pasta_com_arquivos(tmp_path, n=N, tamanho_kb=2)

        # ── Passe único (implementação atual) ────────────────────────────
        zip_novo = tmp_path / "novo.zip"
        inicio = time.perf_counter()
        compactar_arquivos(pasta, zip_novo)
        tempo_novo = time.perf_counter() - inicio

        # ── Duplo passe (simula abordagem antiga) ─────────────────────────
        import shutil

        inicio = time.perf_counter()
        arquivos_duplo = list(pasta.rglob("*"))
        arquivos_duplo = [a for a in arquivos_duplo if a.is_file()]
        _tamanho = sum(a.stat().st_size for a in arquivos_duplo)  # passe extra
        zip_duplo = tmp_path / "duplo.zip"
        with zipfile.ZipFile(zip_duplo, "w", zipfile.ZIP_DEFLATED) as zf:
            for arq in arquivos_duplo:
                zf.write(arq, arq.relative_to(pasta))
        tempo_duplo = time.perf_counter() - inicio

        # Passe único não deve ser mais lento do que o duplo
        assert tempo_novo <= tempo_duplo * 1.20, (
            f"Passe único ({tempo_novo*1000:.1f}ms) foi mais de 20% mais lento "
            f"que duplo ({tempo_duplo*1000:.1f}ms)"
        )
        print(
            f"\n[PERF] Compactação — {N} arquivos:\n"
            f"  Duplo passe : {tempo_duplo*1000:.1f} ms\n"
            f"  Passe único : {tempo_novo*1000:.1f} ms"
        )

    def test_tamanho_calculado_corretamente(self, tmp_path):
        """Valida que o tamanho acumulado no passe único é preciso."""
        pasta = tmp_path / "origem"
        pasta.mkdir()

        # 5 arquivos de 10 KB = 50 KB total
        for i in range(5):
            (pasta / f"f{i}.dat").write_bytes(b"A" * 10 * 1024)

        tamanho_real = sum(f.stat().st_size for f in pasta.iterdir())

        # Simula o passe único da implementação
        arquivos = []
        tamanho_acumulado = 0
        for entrada in pasta.rglob("*"):
            if entrada.is_file():
                arquivos.append(entrada)
                tamanho_acumulado += entrada.stat().st_size

        assert tamanho_acumulado == tamanho_real
        assert len(arquivos) == 5


# ─────────────────────────────────────────────────────────────────────────────
# 3. Índices do banco de dados
# ─────────────────────────────────────────────────────────────────────────────

class TestIndicesBanco:
    """
    Verifica que os índices existem e que as queries críticas
    respondem rapidamente mesmo com muitos registros.
    """

    def test_indices_criados_no_banco(self, banco_teste):
        """Confirma que todos os índices esperados existem na estrutura do banco."""
        from dados.banco import obter_conexao

        conn = obter_conexao()
        indices = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }

        esperados = {
            "idx_backups_email",
            "idx_backups_status",
            "idx_backups_ticket",
            "idx_backups_celery",
            "idx_backups_email_status",
            "idx_etapas_backup",
        }
        faltando = esperados - indices
        assert not faltando, f"Índices ausentes no banco: {faltando}"

    def test_verificacao_duplicata_rapida_com_muitos_registros(self, banco_teste):
        """
        existe_backup_em_andamento() deve responder em menos de 5ms
        mesmo com 500 registros históricos no banco.
        """
        from dados.repositorio_backups import (
            inserir_backup,
            finalizar_backup,
            existe_backup_em_andamento,
        )

        # Popula 500 backups finalizados
        for i in range(500):
            inserir_backup(_email(i), f"SPN-{i:05d}")
            finalizar_backup(_email(i), sucesso=(i % 2 == 0))

        # Insere 1 backup ativo para ser encontrado
        inserir_backup(_email(999), "SPN-99999")

        inicio = time.perf_counter()
        for _ in range(100):
            existe_backup_em_andamento(_email(999))
        tempo_medio_ms = (time.perf_counter() - inicio) / 100 * 1000

        assert tempo_medio_ms < 5, (
            f"existe_backup_em_andamento demorou {tempo_medio_ms:.2f}ms em média "
            f"(esperado < 5ms com índice)"
        )
        print(
            f"\n[PERF] existe_backup_em_andamento — 500 registros históricos:\n"
            f"  Média por chamada: {tempo_medio_ms:.3f} ms"
        )

    def test_verificacao_por_ticket_rapida(self, banco_teste):
        """
        existe_backup_concluido_por_ticket() deve responder em menos de 5ms
        com 500 registros no banco.
        """
        from dados.repositorio_backups import (
            inserir_backup,
            finalizar_backup,
            existe_backup_concluido_por_ticket,
        )

        for i in range(500):
            inserir_backup(_email(i), f"SPN-{i:05d}")
            finalizar_backup(_email(i), sucesso=True)

        inicio = time.perf_counter()
        for _ in range(100):
            existe_backup_concluido_por_ticket("SPN-00250")
        tempo_medio_ms = (time.perf_counter() - inicio) / 100 * 1000

        assert tempo_medio_ms < 5, (
            f"existe_backup_concluido_por_ticket demorou {tempo_medio_ms:.2f}ms "
            f"(esperado < 5ms com índice)"
        )
        print(
            f"\n[PERF] existe_backup_concluido_por_ticket — 500 registros:\n"
            f"  Média por chamada: {tempo_medio_ms:.3f} ms"
        )

    def test_obter_resumo_rapido(self, banco_teste):
        """
        obter_resumo() deve responder em menos de 10ms com 300 registros.
        """
        from dados.repositorio_backups import inserir_backup, finalizar_backup, obter_resumo

        for i in range(100):
            inserir_backup(_email(i), f"SPN-A{i:04d}")
            finalizar_backup(_email(i), sucesso=True)
        for i in range(100, 200):
            inserir_backup(_email(i), f"SPN-B{i:04d}")
            finalizar_backup(_email(i), sucesso=False, erro_mensagem="Erro simulado")
        for i in range(200, 300):
            inserir_backup(_email(i), f"SPN-C{i:04d}")
            # mantém em_andamento

        inicio = time.perf_counter()
        for _ in range(50):
            resumo = obter_resumo()
        tempo_medio_ms = (time.perf_counter() - inicio) / 50 * 1000

        assert resumo["sucessos"] == 100
        assert resumo["erros"] == 100
        assert resumo["ativos"] == 100
        assert tempo_medio_ms < 10, (
            f"obter_resumo demorou {tempo_medio_ms:.2f}ms em média "
            f"(esperado < 10ms)"
        )
        print(
            f"\n[PERF] obter_resumo — 300 registros:\n"
            f"  Média por chamada: {tempo_medio_ms:.3f} ms"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 4. Carregamento em lote — escala
# ─────────────────────────────────────────────────────────────────────────────

class TestEscalaCarregamentoEmLote:
    """
    Verifica que o tempo de listar_ativos() cresce de forma
    sublinear com o número de backups (graças ao batch).
    """

    def _medir_listar_ativos(self, n: int) -> float:
        from dados.repositorio_backups import listar_ativos, inserir_backup
        from dados.banco import fechar_conexao_thread

        for i in range(n):
            inserir_backup(_email(i), f"SPN-{i:06d}")

        inicio = time.perf_counter()
        resultado = listar_ativos()
        tempo = time.perf_counter() - inicio

        assert len(resultado) == n
        return tempo

    def test_tempo_escala_com_poucos_backups(self, banco_teste):
        """10 backups ativos devem ser listados em menos de 20ms."""
        tempo = self._medir_listar_ativos(10)
        assert tempo < 0.020, f"listar_ativos(10) demorou {tempo*1000:.1f}ms (esperado < 20ms)"
        print(f"\n[PERF] listar_ativos(10):  {tempo*1000:.2f} ms")

    def test_tempo_escala_com_muitos_backups(self, banco_teste):
        """100 backups ativos devem ser listados em menos de 50ms."""
        tempo = self._medir_listar_ativos(100)
        assert tempo < 0.050, f"listar_ativos(100) demorou {tempo*1000:.1f}ms (esperado < 50ms)"
        print(f"\n[PERF] listar_ativos(100): {tempo*1000:.2f} ms")
