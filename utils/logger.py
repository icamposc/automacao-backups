"""
============================================================
Módulo de Logging — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-02-19
Descrição: Configura logging centralizado com saída simultânea
           para console (terminal) e arquivo de log rotativo.
           Todos os módulos do projeto usam este logger.
============================================================
Histórico:
  1.0.0 (2026-02-19) — Versão inicial
============================================================
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Pasta de logs (mesmo nível que o módulo config define)
_PASTA_LOGS = Path(__file__).resolve().parent.parent / "logs"
_PASTA_LOGS.mkdir(exist_ok=True)

# Arquivo de log principal
_ARQUIVO_LOG = _PASTA_LOGS / "automacao_backups.log"

# Formato das mensagens de log
# Exemplo: 2026-02-19 14:30:00 | INFO | vault_exportacao | Export criado com sucesso
_FORMATO = "%(asctime)s | %(levelname)-8s | %(name)-25s | %(message)s"
_FORMATO_DATA = "%Y-%m-%d %H:%M:%S"


def obter_logger(nome_modulo: str) -> logging.Logger:
    """
    Retorna um logger configurado para o módulo especificado.

    Cada módulo do projeto deve chamar esta função uma vez:
        logger = obter_logger("nome_do_modulo")
        logger.info("Mensagem de exemplo")

    Args:
        nome_modulo: Nome do módulo que está usando o logger
                     (ex: "vault_exportacao", "orquestrador")

    Returns:
        Logger configurado com saída para console e arquivo
    """
    logger = logging.getLogger(nome_modulo)

    # Evita adicionar handlers duplicados se o logger já foi configurado
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    # --- Handler para Console (terminal) ---
    # Mostra mensagens de INFO para cima no terminal
    handler_console = logging.StreamHandler()
    handler_console.setLevel(logging.INFO)
    handler_console.setFormatter(logging.Formatter(_FORMATO, datefmt=_FORMATO_DATA))

    # --- Handler para Arquivo ---
    # Grava TUDO (inclusive DEBUG) no arquivo de log
    # Rotação: máximo 10 MB por arquivo, mantém 5 arquivos antigos
    handler_arquivo = RotatingFileHandler(
        _ARQUIVO_LOG,
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    handler_arquivo.setLevel(logging.DEBUG)
    handler_arquivo.setFormatter(logging.Formatter(_FORMATO, datefmt=_FORMATO_DATA))

    # Adiciona os dois handlers ao logger
    logger.addHandler(handler_console)
    logger.addHandler(handler_arquivo)

    return logger
