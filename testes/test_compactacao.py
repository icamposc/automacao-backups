"""
Testes do módulo compactacao.py

Verifica:
- Compactação de arquivos em ZIP (ZIP_STORED, sem compressão)
- Retorno em tupla (caminho_zip, sha256_hex)
- Callback de progresso (com throttle)
- Cálculo do SHA256
- Erros esperados (pasta vazia, sem espaço)
"""

import zipfile
import pytest
from pathlib import Path


@pytest.fixture
def pasta_com_arquivos(tmp_path) -> Path:
    """Cria estrutura de arquivos para compactação."""
    pasta = tmp_path / "colaborador"
    (pasta / "email").mkdir(parents=True)
    (pasta / "drive").mkdir()
    (pasta / "email" / "caixa.pst").write_bytes(b"PST" + b"\x00" * 500)
    (pasta / "drive" / "arquivo.pdf").write_bytes(b"PDF" + b"\x00" * 300)
    return pasta


class TestCompactarArquivos:
    def test_cria_zip_valido(self, pasta_com_arquivos, tmp_path):
        from processamento.compactacao import compactar_arquivos
        destino = tmp_path / "backup.zip"
        caminho_zip, sha256_hex = compactar_arquivos(pasta_com_arquivos, destino)

        assert caminho_zip == destino
        assert caminho_zip.exists()
        assert zipfile.is_zipfile(caminho_zip)
        assert len(sha256_hex) == 64

    def test_zip_contem_todos_arquivos(self, pasta_com_arquivos, tmp_path):
        from processamento.compactacao import compactar_arquivos
        destino = tmp_path / "backup.zip"
        compactar_arquivos(pasta_com_arquivos, destino)

        with zipfile.ZipFile(destino) as z:
            nomes = z.namelist()
        assert any("caixa.pst" in n for n in nomes)
        assert any("arquivo.pdf" in n for n in nomes)

    def test_usa_zip_stored_sem_compressao(self, pasta_com_arquivos, tmp_path):
        """PST e ZIP do Vault já vêm comprimidos — DEFLATE só queima CPU.

        Garante que todas as entries no ZIP estão com ZIP_STORED (método 0),
        não ZIP_DEFLATED (método 8). Regredir essa decisão fez o backup do
        David ficar 6h em CPU 100% sem progresso.
        """
        from processamento.compactacao import compactar_arquivos
        destino = tmp_path / "backup.zip"
        compactar_arquivos(pasta_com_arquivos, destino)

        with zipfile.ZipFile(destino) as z:
            for info in z.infolist():
                assert info.compress_type == zipfile.ZIP_STORED, (
                    f"{info.filename} usa compress_type={info.compress_type}, "
                    f"esperado ZIP_STORED ({zipfile.ZIP_STORED})"
                )

    def test_callback_on_progresso_e_invocado(self, pasta_com_arquivos, tmp_path):
        from processamento.compactacao import compactar_arquivos
        destino = tmp_path / "backup.zip"
        chamadas = []

        def _cb(pct: int) -> None:
            chamadas.append(pct)

        compactar_arquivos(pasta_com_arquivos, destino, on_progresso=_cb)

        # Pelo menos a chamada final em 100% deve ter ocorrido.
        assert chamadas, "on_progresso nunca foi chamado"
        assert chamadas[-1] == 100
        # Todos os valores devem estar no intervalo válido.
        assert all(0 <= p <= 100 for p in chamadas)

    def test_callback_que_lanca_excecao_nao_interrompe(self, pasta_com_arquivos, tmp_path):
        """Callback é informativo — falha nele NUNCA pode abortar a compactação."""
        from processamento.compactacao import compactar_arquivos
        destino = tmp_path / "backup.zip"

        def _cb_que_explode(pct: int) -> None:
            raise RuntimeError("erro proposital")

        caminho_zip, sha256_hex = compactar_arquivos(
            pasta_com_arquivos, destino, on_progresso=_cb_que_explode
        )
        assert caminho_zip.exists()
        assert len(sha256_hex) == 64

    def test_retorna_tupla_caminho_e_hash(self, pasta_com_arquivos, tmp_path):
        from processamento.compactacao import compactar_arquivos
        destino = tmp_path / "backup.zip"
        resultado = compactar_arquivos(pasta_com_arquivos, destino)

        assert isinstance(resultado, tuple)
        assert len(resultado) == 2
        caminho_zip, sha256_hex = resultado
        assert isinstance(caminho_zip, Path)
        assert isinstance(sha256_hex, str)

    def test_pasta_vazia_levanta_excecao(self, tmp_path):
        from processamento.compactacao import compactar_arquivos
        pasta_vazia = tmp_path / "vazia"
        pasta_vazia.mkdir()
        with pytest.raises(Exception, match="vazia"):
            compactar_arquivos(pasta_vazia, tmp_path / "out.zip")

    def test_pasta_inexistente_levanta_excecao(self, tmp_path):
        from processamento.compactacao import compactar_arquivos
        with pytest.raises(Exception, match="não encontrada"):
            compactar_arquivos(tmp_path / "nao_existe", tmp_path / "out.zip")


class TestCalcularSha256:
    def test_hash_consistente(self, tmp_path):
        from processamento.compactacao import calcular_sha256
        arquivo = tmp_path / "teste.zip"
        arquivo.write_bytes(b"conteudo fixo para hash")

        hash1 = calcular_sha256(arquivo)
        hash2 = calcular_sha256(arquivo)
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 em hex = 64 chars

    def test_arquivos_diferentes_tem_hashes_diferentes(self, tmp_path):
        from processamento.compactacao import calcular_sha256
        arq1 = tmp_path / "a.zip"
        arq2 = tmp_path / "b.zip"
        arq1.write_bytes(b"conteudo A")
        arq2.write_bytes(b"conteudo B")

        assert calcular_sha256(arq1) != calcular_sha256(arq2)
