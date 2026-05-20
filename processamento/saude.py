"""
============================================================
Módulo de Saúde — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-05-19
Descrição: Coletor de status dos componentes do sistema
           (banco, Redis, Celery, disco, backups travados) e
           monitor periódico que dispara alertas no Google
           Chat de LOGS quando o sistema transita entre
           estados (ok ↔ degradado).

           Reaproveita a mesma lógica antes embutida no
           endpoint /health do app/servidor.py.
============================================================
Histórico:
  1.0.0 (2026-05-19) — Versão inicial: extração do /health
                        + monitor de transição.
============================================================
"""

import shutil
import threading
import time
from datetime import datetime
from typing import Tuple

from utils.logger import obter_logger

logger = obter_logger("saude")

# Threshold de disco livre (%); abaixo disso, "degradado".
_DISCO_MIN_LIVRE_PCT = 20

# Threshold de disco que escala WARNING para CRITICAL.
_DISCO_CRITICAL_LIVRE_PCT = 5

# Limite acima do qual um backup em_andamento é considerado "stuck".
_STUCK_HORAS = 12

# Intervalo entre verificações do monitor (segundos).
_INTERVALO_MONITOR = 300  # 5 minutos

# Tempo mínimo entre re-alertas quando o sistema permanece em CRITICAL (segundos).
# WARNING não tem re-alerta — só dispara na transição para reduzir ruído.
_REALERT_INTERVALO = 3600  # 1 hora

# Componentes cuja indisponibilidade é considerada CRITICAL
# (sistema fica fundamentalmente quebrado sem eles).
_COMPONENTES_CRITICAL = {"redis", "banco"}


def coletar_status_saude() -> Tuple[str, dict]:
    """
    Coleta o estado atual de todos os componentes monitorados.

    Returns:
        (status_geral, payload) onde:
          status_geral ∈ {"ok", "degradado"}
          payload contém os campos consumidos pelo endpoint /health
    """
    # Imports locais — evita custo de carregar tudo só para responder /health
    from processamento.rastreador import obter_resumo, obter_historico
    from dados.repositorio_backups import listar_backups_stuck
    from config.configuracoes import REDIS_URL, PASTA_VAULT

    componentes = {
        "servidor": "ok",
        "banco":    "desconhecido",
        "redis":    "desconhecido",
        "celery":   "desconhecido",
        "disco":    "desconhecido",
    }
    status_geral = "ok"

    # ── Banco ─────────────────────────────────────────────────────────────
    try:
        resumo = obter_resumo()
        componentes["banco"] = "ok"
    except Exception as erro:
        componentes["banco"] = f"erro: {erro}"
        resumo = {"ativos": 0, "total_finalizados": 0, "sucessos": 0, "erros": 0}
        status_geral = "degradado"

    # ── Redis ─────────────────────────────────────────────────────────────
    try:
        import redis
        r = redis.from_url(REDIS_URL, socket_connect_timeout=2)
        r.ping()
        componentes["redis"] = "ok"
    except Exception as erro:
        componentes["redis"] = f"indisponivel: {erro}"
        status_geral = "degradado"

    # ── Celery ────────────────────────────────────────────────────────────
    try:
        from worker.celery_app import app as celery_app
        # timeout=1s e suficiente para workers locais (rede docker, resposta < 50ms)
        # e ainda absorve picos de carga. O `inspect.ping()` do Celery SEMPRE
        # espera o timeout inteiro para coletar respostas via Redis pub/sub,
        # entao timeout maior so adiciona latencia ao /health sem ganho real.
        # Antes era 5s e fazia o endpoint demorar ~6s em cada request.
        pongs = celery_app.control.inspect(timeout=1).ping()
        if pongs:
            componentes["celery"] = f"ok ({len(pongs)} worker(s))"
        else:
            componentes["celery"] = "sem_workers"
            status_geral = "degradado"
    except Exception as erro:
        componentes["celery"] = f"erro: {erro}"
        status_geral = "degradado"

    # ── Disco ─────────────────────────────────────────────────────────────
    try:
        uso = shutil.disk_usage(PASTA_VAULT)
        livre_pct = (uso.free / uso.total * 100) if uso.total else 0
        disco_info = {
            "total_gb": round(uso.total / (1024 ** 3), 2),
            "livre_gb": round(uso.free / (1024 ** 3), 2),
            "livre_pct": round(livre_pct, 1),
        }
        if livre_pct < _DISCO_MIN_LIVRE_PCT:
            componentes["disco"] = f"degradado: {livre_pct:.1f}% livre"
            status_geral = "degradado"
        else:
            componentes["disco"] = "ok"
        componentes["disco_detalhe"] = disco_info
    except Exception as erro:
        componentes["disco"] = f"erro: {erro}"
        status_geral = "degradado"

    # ── Backups stuck ─────────────────────────────────────────────────────
    stuck = []
    try:
        stuck = listar_backups_stuck(horas=_STUCK_HORAS)
        if stuck:
            status_geral = "degradado"
    except Exception as erro:
        logger.warning(f"Falha ao listar backups stuck: {erro}")

    # ── Última execução ───────────────────────────────────────────────────
    ultima = None
    try:
        historico = obter_historico(por_pagina=1)
        if historico:
            ultimo = historico[0]
            ultima = {
                "email":  ultimo.get("email"),
                "status": ultimo.get("status_geral"),
                "fim":    ultimo.get("fim"),
            }
    except Exception:
        pass

    payload = {
        "status":               status_geral,
        "versao":               "2.0.0",
        "timestamp":            datetime.now().isoformat(),
        "componentes":          componentes,
        "backups_em_andamento": resumo.get("ativos", 0),
        "backups_stuck":        stuck,
        "resumo":               resumo,
        "ultima_execucao":      ultima,
    }
    return status_geral, payload


def _extrair_problemas(componentes: dict) -> dict:
    """Filtra apenas os componentes em estado não-ok para o alerta."""
    problemas = {}
    for nome, valor in componentes.items():
        if nome == "disco_detalhe":
            continue
        if not isinstance(valor, str):
            continue
        if not valor.startswith("ok") and valor != "ok":
            problemas[nome] = valor
    return problemas


def _classificar_nivel(problemas: dict, disco_detalhe: dict, stuck: list) -> str | None:
    """
    Classifica o estado atual em 'critical', 'warning' ou None.

    CRITICAL — situações em que o sistema está fundamentalmente quebrado
    ou prestes a quebrar:
      • Redis ou banco SQLite indisponível (fila parada / sem coordenação)
      • Disco com menos de _DISCO_CRITICAL_LIVRE_PCT% livre
      • Qualquer backup travado (status='em_andamento' há > 12h)

    WARNING — sistema degradado mas operacional:
      • Celery sem workers (operação humana pode subir o worker)
      • Disco entre 5% e 20% livre
      • Outros componentes com erro transitório

    None — nada relevante para alertar (silencia o monitor).
    """
    if not problemas and not stuck:
        return None

    # Backups travados sempre indicam algo errado de forma persistente
    if stuck:
        return "critical"

    # Componentes na lista de críticos
    for componente in _COMPONENTES_CRITICAL:
        if componente in problemas:
            return "critical"

    # Disco abaixo do threshold crítico
    if disco_detalhe:
        livre_pct = disco_detalhe.get("livre_pct", 100)
        if livre_pct < _DISCO_CRITICAL_LIVRE_PCT:
            return "critical"

    # Qualquer outro problema (celery sem workers, disco 5-20%, outros) é WARNING
    return "warning"


# ── Monitor periódico ────────────────────────────────────────────────────

# Estado em memória: último nível conhecido + último timestamp de alerta.
# nivel_anterior ∈ {None (ok), "warning", "critical"}.
_estado = {
    "nivel_anterior": None,
    "problemas_anteriores": {},
    "ultimo_alerta_ts": 0.0,
}
_lock = threading.Lock()


def _ciclo_monitor() -> None:
    """Loop principal do monitor (executado em thread daemon)."""
    # Espera inicial para o servidor estabilizar (banco, Redis, worker)
    time.sleep(30)

    while True:
        try:
            _verificar_e_alertar()
        except Exception as erro:
            logger.error(f"Erro no ciclo do monitor de saúde: {erro}", exc_info=True)
        time.sleep(_INTERVALO_MONITOR)


def _verificar_e_alertar() -> None:
    """
    Faz uma checagem e dispara alertas conforme as transições de NÍVEL.

    Regras de envio:
      • Transição de nível (None ↔ warning ↔ critical): notifica.
      • Permanência em CRITICAL por _REALERT_INTERVALO sem aviso: re-alerta.
      • Permanência em WARNING: silencioso (sem re-alerta — reduz ruído).
      • Permanência em OK: silencioso.
    """
    from servicos.google_chat import (
        notificar_saude_degradada,
        notificar_saude_recuperada,
    )

    _status, payload = coletar_status_saude()
    componentes = payload["componentes"]
    problemas = _extrair_problemas(componentes)
    detalhes_disco = componentes.get("disco_detalhe")
    stuck = payload.get("backups_stuck") or []
    nivel = _classificar_nivel(problemas, detalhes_disco, stuck)
    agora = time.time()

    with _lock:
        nivel_anterior = _estado["nivel_anterior"]
        problemas_anteriores = _estado["problemas_anteriores"]
        ultimo_alerta = _estado["ultimo_alerta_ts"]

        # Caso 1: transição de nível (mudou qualquer coisa relevante)
        if nivel != nivel_anterior:
            if nivel is None:
                # Voltou para OK — notifica recuperação
                logger.info("Saúde transitou para OK (recuperado)")
                notificar_saude_recuperada(list(problemas_anteriores.keys()))
                _estado["ultimo_alerta_ts"] = 0.0
            else:
                # Entrou em warning/critical, ou escalou warning→critical, ou recuou critical→warning
                logger.warning(
                    f"Saúde transitou para {nivel.upper()}: "
                    f"{problemas} stuck={len(stuck)}"
                )
                notificar_saude_degradada(problemas, detalhes_disco, nivel=nivel)
                _estado["ultimo_alerta_ts"] = agora

        # Caso 2: permaneceu em CRITICAL — re-alerta horário
        elif nivel == "critical" and (agora - ultimo_alerta) >= _REALERT_INTERVALO:
            logger.warning(f"Re-alerta CRITICAL: {problemas} stuck={len(stuck)}")
            notificar_saude_degradada(problemas, detalhes_disco, nivel="critical")
            _estado["ultimo_alerta_ts"] = agora

        # Caso 3: permaneceu em WARNING ou OK → silencioso (sem ação)

        _estado["nivel_anterior"] = nivel
        _estado["problemas_anteriores"] = problemas


def iniciar_monitor_saude() -> None:
    """Sobe a thread daemon que monitora saúde periodicamente.

    Idempotente: se já houver uma thread ativa do monitor, não cria outra.
    """
    nome_thread = "monitor-saude"
    for t in threading.enumerate():
        if t.name == nome_thread and t.is_alive():
            logger.debug("Monitor de saúde já está rodando — ignorando segunda inicialização")
            return

    thread = threading.Thread(target=_ciclo_monitor, name=nome_thread, daemon=True)
    thread.start()
    logger.info(
        f"Monitor de saúde iniciado — intervalo={_INTERVALO_MONITOR}s, "
        f"níveis: warning (1x na transição) e critical (re-alerta a cada {_REALERT_INTERVALO}s)"
    )
