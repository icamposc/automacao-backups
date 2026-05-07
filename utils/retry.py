"""
============================================================
Módulo de Retry com Backoff Exponencial — Automação de Backups
============================================================
Versão: 1.1.0
Data: 2026-05-07
Descrição: Utilitário para calcular tempo de espera crescente
           entre tentativas de retry e para encapsular chamadas
           HTTP que podem falhar por sockets mortos durante
           polls longos (ex: monitoramento de exports do Vault).
============================================================
"""

import socket
import ssl
import time
from http.client import RemoteDisconnected, BadStatusLine

from utils.logger import obter_logger

logger = obter_logger("retry")


# Erros transientes de rede em chamadas HTTPS de longa duração.
# Surgem quando uma conexão TCP fica ociosa (>5 min) e é derrubada
# pelo lado remoto / NAT / load balancer antes da próxima requisição.
ERROS_TRANSIENTES_REDE = (
    BrokenPipeError,
    ConnectionResetError,
    ConnectionAbortedError,
    socket.timeout,
    ssl.SSLError,
    RemoteDisconnected,
    BadStatusLine,
    OSError,
)


def calcular_backoff(tentativa: int, base: int = 30, multiplicador: int = 2, maximo: int = 300) -> int:
    """
    Calcula o tempo de espera com backoff exponencial.

    Progressão com valores padrão:
      Tentativa 1 → 30s
      Tentativa 2 → 60s
      Tentativa 3 → 120s
      Tentativa 4 → 240s
      Tentativa 5+ → 300s (cap)

    Args:
        tentativa:    Número da tentativa atual (começa em 1)
        base:         Tempo base em segundos (padrão: 30)
        multiplicador: Fator de crescimento (padrão: 2)
        maximo:       Tempo máximo de espera em segundos (padrão: 300)

    Returns:
        Tempo de espera em segundos
    """
    return min(base * (multiplicador ** (tentativa - 1)), maximo)


def chamar_com_retry_rede(fn_chamada, fn_recriar=None, max_tentativas: int = 5, contexto: str = ""):
    """
    Executa uma chamada HTTP com retry contra erros transientes de rede.

    Pensado para chamadas Google API (httplib2) feitas em loops longos —
    o socket pode morrer durante o intervalo ocioso e a próxima requisição
    falha com BrokenPipe/ConnectionReset/SSLError ao tentar enviar dados
    sobre a conexão zumbi. O retry recria o cliente HTTP entre tentativas
    para garantir uma conexão TCP fresca.

    Args:
        fn_chamada:     Callable sem argumentos que executa a requisição.
        fn_recriar:     Callable opcional sem argumentos chamado entre
                        tentativas para reconstruir o cliente HTTP/serviço.
                        Se None, apenas espera e tenta de novo.
        max_tentativas: Número máximo de tentativas (padrão: 5).
        contexto:       Texto opcional incluído nos logs (ex: export_id).

    Returns:
        Resultado de fn_chamada() na primeira tentativa bem-sucedida.

    Raises:
        A última exceção transiente observada se todas as tentativas falharem.
        Qualquer exceção não-transiente é propagada imediatamente.
    """
    ultima_excecao = None
    for tentativa in range(1, max_tentativas + 1):
        try:
            return fn_chamada()
        except ERROS_TRANSIENTES_REDE as erro:
            ultima_excecao = erro
            if tentativa == max_tentativas:
                logger.error(
                    f"Falha de rede após {max_tentativas} tentativas{f' ({contexto})' if contexto else ''}: {erro!r}"
                )
                raise
            espera = calcular_backoff(tentativa, base=5, maximo=60)
            logger.warning(
                f"Erro de rede na tentativa {tentativa}/{max_tentativas}"
                f"{f' ({contexto})' if contexto else ''}: {erro!r}. "
                f"Recriando cliente e aguardando {espera}s."
            )
            if fn_recriar is not None:
                try:
                    fn_recriar()
                except Exception as erro_recriacao:
                    logger.warning(f"Falha ao recriar cliente HTTP: {erro_recriacao!r}")
            time.sleep(espera)
    # Defensivo: nunca deveria chegar aqui, mas se chegar, propaga a última exceção.
    if ultima_excecao is not None:
        raise ultima_excecao
