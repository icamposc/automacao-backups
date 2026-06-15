"""
============================================================
Módulo de Auditoria — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-06-15
Descrição: Registra, em trilha de auditoria separada, as ações
           sensíveis executadas por usuários autenticados
           (login/logout e disparos de backup manual, lote e
           refazer), incluindo QUEM, O QUÊ, DE ONDE e QUANDO.

           A trilha vai para logs/auditoria/auditoria.log — um
           subdiretório que a rotina de limpeza de logs (glob
           não-recursivo em *.log*) NÃO alcança, preservando o
           histórico de auditoria.
============================================================
Histórico:
  1.0.0 (2026-06-15) — Versão inicial
============================================================
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import session, request, has_request_context

# Trilha em subdiretório próprio — fora do alcance do limpar_logs_antigos,
# que faz glob não-recursivo "*.log*" na raiz de PASTA_LOGS.
_PASTA_AUDITORIA = Path(__file__).resolve().parent.parent / "logs" / "auditoria"
_PASTA_AUDITORIA.mkdir(parents=True, exist_ok=True)
_ARQUIVO_AUDITORIA = _PASTA_AUDITORIA / "auditoria.log"

_FORMATO = "%(asctime)s | %(message)s"
_FORMATO_DATA = "%Y-%m-%d %H:%M:%S"


def _obter_logger_auditoria() -> logging.Logger:
    """Logger dedicado da auditoria (arquivo próprio + console)."""
    logger = logging.getLogger("auditoria")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    # Não propaga para o root/log principal — a trilha fica isolada.
    logger.propagate = False

    # Arquivo dedicado, com retenção ampla (20 arquivos rotativos de 10 MB).
    handler_arquivo = RotatingFileHandler(
        _ARQUIVO_AUDITORIA,
        maxBytes=10 * 1024 * 1024,
        backupCount=20,
        encoding="utf-8",
    )
    handler_arquivo.setFormatter(logging.Formatter(_FORMATO, datefmt=_FORMATO_DATA))
    logger.addHandler(handler_arquivo)

    # Eco no console (stdout) — aparece também nos logs do container/Gunicorn.
    handler_console = logging.StreamHandler()
    handler_console.setFormatter(
        logging.Formatter("%(asctime)s | AUDITORIA | %(message)s", datefmt=_FORMATO_DATA)
    )
    logger.addHandler(handler_console)

    return logger


def _contexto() -> tuple[str, str, str]:
    """Extrai (usuario, tipo, ip) da sessão/requisição atual."""
    if has_request_context():
        usuario = session.get("usuario") or "anonimo"
        tipo = session.get("tipo") or "?"
        # remote_addr é a origem direta; X-Forwarded-For só é confiável atrás
        # de proxy próprio. Registramos os dois quando o header existir.
        ip = request.remote_addr or "?"
        xff = request.headers.get("X-Forwarded-For")
        if xff:
            ip = f"{ip} (xff={xff})"
        return usuario, tipo, ip
    return "sistema", "sistema", "-"


def _sanitizar(valor) -> str:
    """Evita quebra de linha/pipe que atrapalhe o parsing da trilha."""
    return str(valor).replace("|", "/").replace("\n", " ").replace("\r", " ").strip()


def registrar(acao: str, resultado: str = "ok", **detalhes) -> None:
    """
    Registra uma entrada na trilha de auditoria.

    Args:
        acao:      identificador curto da ação (ex.: "login", "backup_manual").
        resultado: desfecho (ex.: "sucesso", "negado", "erro", "ok").
        detalhes:  pares chave=valor adicionais (ex.: email=..., ticket=...).

    A entrada inclui automaticamente usuário, tipo (ad/local), IP e horário.
    Nunca lança exceção — auditoria não pode derrubar a ação em si.
    """
    try:
        usuario, tipo, ip = _contexto()
        partes = [
            f"usuario={_sanitizar(usuario)}",
            f"tipo={_sanitizar(tipo)}",
            f"ip={_sanitizar(ip)}",
            f"acao={_sanitizar(acao)}",
            f"resultado={_sanitizar(resultado)}",
        ]
        for chave, valor in detalhes.items():
            partes.append(f"{chave}={_sanitizar(valor)}")
        _obter_logger_auditoria().info(" | ".join(partes))
    except Exception:
        # Falha de auditoria jamais deve interromper o fluxo da requisição.
        logging.getLogger("auditoria").debug("Falha ao registrar auditoria", exc_info=True)
