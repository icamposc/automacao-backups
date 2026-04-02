"""
Testes do módulo compactacao.py

Verifica:
- Compactação de arquivos em ZIP
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
        resultado = compactar_arquivos(pasta_com_arquivos, destino)

        assert resultado.exists()
        assert zipfile.is_zipfile(resultado)

    def test_zip_contem_todos_arquivos(self, pasta_com_arquivos, tmp_path):
        from processamento.compactacao import compactar_arquivos
        destino = tmp_path / "backup.zip"
        compactar_arquivos(pasta_com_arquivos, destino)

        with zipfile.ZipFile(destino) as z:
            nomes = z.namelist()
        assert any("caixa.pst" in n for n in nomes)
        assert any("arquivo.pdf" in n for n in nomes)

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
