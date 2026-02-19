"""
============================================================
Testes do Google Drive — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-02-19
Descrição: Testes de integração para o módulo drive_upload.
           Testa compactação de arquivos e upload para o
           Google Drive Compartilhado.

           Uso: pytest testes/teste_drive.py -v
           (requer .env configurado e Service Account válida)
============================================================
Histórico:
  1.0.0 (2026-02-19) — Versão inicial
============================================================
"""

import pytest
from pathlib import Path
import tempfile


class TestCompactacao:
    """Testes unitários para o módulo de compactação."""

    def test_compactar_pasta_com_arquivos(self):
        """Testa compactação de uma pasta com arquivos de teste."""
        from processamento.compactacao import compactar_arquivos

        # Cria uma pasta temporária com arquivos de teste
        with tempfile.TemporaryDirectory() as pasta_temp:
            pasta_origem = Path(pasta_temp) / "origem"
            pasta_origem.mkdir()

            # Cria alguns arquivos de teste
            (pasta_origem / "arquivo1.txt").write_text("Conteúdo do arquivo 1")
            (pasta_origem / "arquivo2.txt").write_text("Conteúdo do arquivo 2")

            # Cria subpasta com arquivo
            subpasta = pasta_origem / "subpasta"
            subpasta.mkdir()
            (subpasta / "arquivo3.txt").write_text("Conteúdo do arquivo 3")

            # Caminho para o ZIP
            caminho_zip = Path(pasta_temp) / "teste.zip"

            # Compacta
            resultado = compactar_arquivos(pasta_origem, caminho_zip)

            # Verifica
            assert resultado.exists()
            assert resultado.stat().st_size > 0
            print(f"ZIP criado: {resultado} ({resultado.stat().st_size} bytes)")

    def test_compactar_pasta_vazia_deve_falhar(self):
        """Testa que compactar pasta vazia gera exceção."""
        from processamento.compactacao import compactar_arquivos

        with tempfile.TemporaryDirectory() as pasta_temp:
            pasta_vazia = Path(pasta_temp) / "vazia"
            pasta_vazia.mkdir()
            caminho_zip = Path(pasta_temp) / "teste.zip"

            with pytest.raises(Exception, match="vazia"):
                compactar_arquivos(pasta_vazia, caminho_zip)

    def test_compactar_pasta_inexistente_deve_falhar(self):
        """Testa que compactar pasta inexistente gera exceção."""
        from processamento.compactacao import compactar_arquivos

        with tempfile.TemporaryDirectory() as pasta_temp:
            pasta_fake = Path(pasta_temp) / "nao_existe"
            caminho_zip = Path(pasta_temp) / "teste.zip"

            with pytest.raises(Exception, match="não encontrada"):
                compactar_arquivos(pasta_fake, caminho_zip)


class TestDriveUpload:
    """
    Testes de integração para upload ao Google Drive.

    ATENÇÃO: Estes testes fazem upload REAL para o Google Drive.
    Use apenas em ambiente controlado.
    """

    @pytest.mark.skipif(
        not Path(".env").exists(),
        reason="Arquivo .env não encontrado — testes de integração requerem configuração",
    )
    def test_upload_arquivo_pequeno(self):
        """Testa upload de um arquivo .zip pequeno para o Drive."""
        from servicos.drive_upload import fazer_upload
        from processamento.compactacao import compactar_arquivos

        with tempfile.TemporaryDirectory() as pasta_temp:
            # Cria arquivo de teste
            pasta_origem = Path(pasta_temp) / "teste"
            pasta_origem.mkdir()
            (pasta_origem / "teste.txt").write_text("Arquivo de teste para upload")

            # Compacta
            caminho_zip = Path(pasta_temp) / "teste_upload.zip"
            compactar_arquivos(pasta_origem, caminho_zip)

            # Faz upload
            resultado = fazer_upload(caminho_zip, "teste_automacao.zip")

            assert resultado is not None
            assert "id" in resultado
            print(f"Upload concluído — ID: {resultado['id']}")
            print(f"Link: {resultado.get('webViewLink', 'N/A')}")
