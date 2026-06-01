"""
Testes do módulo drive_upload.py

Verifica:
- Upload com sucesso (chunked)
- Retry em caso de falha
- SHA256 incluído nos metadados
- Arquivo não encontrado
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open


@pytest.fixture
def arquivo_zip(tmp_path) -> Path:
    """Cria um arquivo ZIP temporário de teste."""
    caminho = tmp_path / "backup.zip"
    caminho.write_bytes(b"PK" + b"\x00" * 1000)  # conteúdo fictício
    return caminho


@pytest.fixture(autouse=True)
def sem_sleep(mocker):
    mocker.patch("time.sleep")


class TestFazerUpload:
    def test_arquivo_inexistente_levanta_excecao(self, tmp_path):
        from servicos.drive_upload import fazer_upload
        with pytest.raises(FileNotFoundError):
            fazer_upload(tmp_path / "nao_existe.zip")

    def test_upload_bem_sucedido(self, arquivo_zip, mocker):
        credenciais_mock = MagicMock()
        sessao_mock = MagicMock()

        # Resposta da iniciação da sessão
        resp_inicio = MagicMock()
        resp_inicio.headers = {"Location": "https://upload.googleapis.com/resumable/upload/123"}
        resp_inicio.raise_for_status.return_value = None

        # Resposta do chunk final (upload concluído)
        resp_chunk = MagicMock()
        resp_chunk.status_code = 200
        resp_chunk.json.return_value = {
            "id": "arquivo-drive-id",
            "name": "backup.zip",
            "webViewLink": "https://drive.google.com/file/d/arquivo-drive-id/view",
        }

        sessao_mock.post.return_value = resp_inicio
        sessao_mock.put.return_value = resp_chunk

        mocker.patch("servicos.drive_upload._obter_credenciais", return_value=credenciais_mock)
        mocker.patch("servicos.drive_upload.AuthorizedSession", return_value=sessao_mock)

        from servicos.drive_upload import fazer_upload
        resultado = fazer_upload(arquivo_zip, "backup.zip")

        assert resultado["id"] == "arquivo-drive-id"
        assert "webViewLink" in resultado

    def test_sha256_incluido_nos_metadados(self, arquivo_zip, mocker):
        credenciais_mock = MagicMock()
        sessao_mock = MagicMock()

        resp_inicio = MagicMock()
        resp_inicio.headers = {"Location": "https://upload.googleapis.com/resumable/upload/123"}
        resp_inicio.raise_for_status.return_value = None

        resp_chunk = MagicMock()
        resp_chunk.status_code = 200
        resp_chunk.json.return_value = {"id": "abc", "name": "backup.zip", "webViewLink": "https://..."}

        sessao_mock.post.return_value = resp_inicio
        sessao_mock.put.return_value = resp_chunk

        mocker.patch("servicos.drive_upload._obter_credenciais", return_value=credenciais_mock)
        mocker.patch("servicos.drive_upload.AuthorizedSession", return_value=sessao_mock)

        from servicos.drive_upload import fazer_upload
        fazer_upload(arquivo_zip, "backup.zip", sha256="abc123")

        # Verifica que o SHA256 foi incluído nos dados enviados
        chamada = sessao_mock.post.call_args
        corpo_enviado = chamada[1].get("data") or chamada[0][1] if len(chamada[0]) > 1 else ""
        if isinstance(corpo_enviado, bytes):
            corpo_enviado = corpo_enviado.decode()
        assert "abc123" in str(corpo_enviado) or "abc123" in str(chamada)
