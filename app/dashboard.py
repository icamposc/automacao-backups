"""
============================================================
Módulo Dashboard — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-03-10
Descrição: Blueprint Flask que expõe o painel de acompanhamento
           dos backups em tempo real. Fornece:
           - GET /dashboard → Página HTML do painel
           - GET /api/backups/ativos → JSON com backups em andamento
           - GET /api/backups/historico → JSON com backups finalizados
           - GET /api/backups/resumo → JSON com resumo geral
============================================================
Histórico:
  1.0.0 (2026-03-10) — Versão inicial
============================================================
"""

from flask import Blueprint, jsonify, render_template

from processamento.rastreador import (
    obter_backups_ativos,
    obter_historico,
    obter_resumo,
    obter_backup,
)
from utils.logger import obter_logger

logger = obter_logger("dashboard")

bp = Blueprint(
    "dashboard",
    __name__,
    template_folder="templates",
    static_folder="static",
)


@bp.route("/dashboard")
def painel():
    """Renderiza a página HTML do dashboard."""
    return render_template("dashboard.html")


@bp.route("/api/backups/ativos")
def api_ativos():
    """Retorna a lista de backups em andamento (JSON)."""
    return jsonify(obter_backups_ativos())


@bp.route("/api/backups/historico")
def api_historico():
    """Retorna o histórico de backups finalizados (JSON)."""
    return jsonify(obter_historico())


@bp.route("/api/backups/resumo")
def api_resumo():
    """Retorna resumo geral dos backups (JSON)."""
    return jsonify(obter_resumo())


@bp.route("/api/backups/<email>")
def api_detalhe(email):
    """Retorna dados detalhados de um backup específico."""
    backup = obter_backup(email)
    if not backup:
        return jsonify({"erro": "Backup não encontrado"}), 404
    return jsonify(backup)
