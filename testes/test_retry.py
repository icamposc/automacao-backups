"""
Testes do módulo utils/retry.py — foco em chamar_com_retry_rede.

Verifica que erros transientes de rede (BrokenPipe, ConnectionReset,
SSLError, etc.) são absorvidos com retry e que o callback de
recriação do cliente é chamado entre tentativas.
"""

import socket
import ssl

import pytest


@pytest.fixture(autouse=True)
def sem_sleep(mocker):
    mocker.patch("time.sleep")


class TestChamarComRetryRede:
    def test_sucesso_primeira_tentativa(self):
        from utils.retry import chamar_com_retry_rede

        chamadas = []

        def fn():
            chamadas.append(1)
            return "ok"

        resultado = chamar_com_retry_rede(fn, fn_recriar=None, max_tentativas=3)
        assert resultado == "ok"
        assert len(chamadas) == 1

    def test_recupera_de_broken_pipe(self):
        from utils.retry import chamar_com_retry_rede

        tentativas = {"n": 0}
        recriacoes = {"n": 0}

        def fn():
            tentativas["n"] += 1
            if tentativas["n"] < 3:
                raise BrokenPipeError("[Errno 32] Broken pipe")
            return {"id": "export-123", "status": "IN_PROGRESS"}

        def recriar():
            recriacoes["n"] += 1

        resultado = chamar_com_retry_rede(fn, fn_recriar=recriar, max_tentativas=5)
        assert resultado["status"] == "IN_PROGRESS"
        assert tentativas["n"] == 3
        assert recriacoes["n"] == 2  # uma recriação por falha

    def test_recupera_de_ssl_error(self):
        from utils.retry import chamar_com_retry_rede

        tentativas = {"n": 0}

        def fn():
            tentativas["n"] += 1
            if tentativas["n"] == 1:
                raise ssl.SSLError("[SSL: SSLV3_ALERT_BAD_RECORD_MAC] ssl/tls alert bad record mac")
            return "ok"

        assert chamar_com_retry_rede(fn, max_tentativas=3) == "ok"
        assert tentativas["n"] == 2

    def test_recupera_de_connection_reset(self):
        from utils.retry import chamar_com_retry_rede

        tentativas = {"n": 0}

        def fn():
            tentativas["n"] += 1
            if tentativas["n"] == 1:
                raise ConnectionResetError("Connection reset by peer")
            return "ok"

        assert chamar_com_retry_rede(fn) == "ok"
        assert tentativas["n"] == 2

    def test_recupera_de_socket_timeout(self):
        from utils.retry import chamar_com_retry_rede

        tentativas = {"n": 0}

        def fn():
            tentativas["n"] += 1
            if tentativas["n"] == 1:
                raise socket.timeout("timed out")
            return "ok"

        assert chamar_com_retry_rede(fn) == "ok"

    def test_propaga_excecao_apos_max_tentativas(self):
        from utils.retry import chamar_com_retry_rede

        def fn():
            raise BrokenPipeError("nunca recupera")

        with pytest.raises(BrokenPipeError, match="nunca recupera"):
            chamar_com_retry_rede(fn, max_tentativas=3)

    def test_propaga_excecao_nao_transiente(self):
        from utils.retry import chamar_com_retry_rede

        def fn():
            raise ValueError("erro de negócio")

        # Erro não-transiente é propagado na primeira tentativa, sem retry
        with pytest.raises(ValueError, match="erro de negócio"):
            chamar_com_retry_rede(fn, max_tentativas=5)

    def test_falha_na_recriacao_nao_quebra_retry(self):
        from utils.retry import chamar_com_retry_rede

        tentativas = {"n": 0}

        def fn():
            tentativas["n"] += 1
            if tentativas["n"] == 1:
                raise BrokenPipeError("primeiro")
            return "ok"

        def recriar_quebrado():
            raise RuntimeError("falha ao recriar cliente")

        # Recriação que falha não deve impedir o retry — apenas é logada
        assert chamar_com_retry_rede(fn, fn_recriar=recriar_quebrado, max_tentativas=3) == "ok"
