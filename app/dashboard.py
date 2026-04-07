"""
============================================================
Módulo Dashboard — Automação de Backups
============================================================
Versão: 2.0.0
Data: 2026-04-02
Descrição: Blueprint Flask que expõe o painel de acompanhamento.
           A partir da v2.0.0, o histórico é lido do banco de
           dados SQLite e suporta paginação.

           Rotas:
           - GET /dashboard                → Página HTML
           - GET /api/backups/ativos       → JSON backups em andamento
           - GET /api/backups/historico    → JSON histórico (paginado)
           - GET /api/backups/resumo       → JSON resumo geral
           - GET /api/backups/<email>      → JSON backup específico
============================================================
Histórico:
  2.0.0 (2026-04-02) — Paginação via SQLite (melhoria #6)
  1.0.0 (2026-03-10) — Versão inicial
============================================================
"""

import re
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request

from processamento.rastreador import (
    obter_backups_ativos,
    obter_historico,
    obter_resumo,
    obter_backup,
)
from processamento.orquestrador import iniciar_backup_async, esta_em_processamento
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
    """
    Retorna o histórico de backups finalizados (JSON) com paginação.

    Query params:
    - pagina (int, padrão 1): página desejada
    - por_pagina (int, padrão 50): registros por página (máx 200)
    """
    try:
        pagina = int(request.args.get("pagina", 1))
        por_pagina = min(int(request.args.get("por_pagina", 50)), 200)
        if pagina < 1:
            pagina = 1
    except (ValueError, TypeError):
        pagina, por_pagina = 1, 50

    historico = obter_historico(pagina=pagina, por_pagina=por_pagina)

    return jsonify({
        "pagina":      pagina,
        "por_pagina":  por_pagina,
        "total_pagina": len(historico),
        "dados":       historico,
    })


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


@bp.route("/api/backups/iniciar", methods=["POST"])
def api_iniciar_manual():
    """
    Dispara um backup manualmente sem depender do webhook do Jira.

    Body JSON:
        email     (obrigatório): e-mail corporativo do colaborador
        nome      (opcional):    nome completo
        ticket_id (opcional):    ticket Jira; se omitido, gera MANUAL-{timestamp}

    Retorna 409 se já houver backup em andamento para o e-mail.
    """
    dados = request.get_json(silent=True) or {}

    email = (dados.get("email") or "").strip().lower()
    if not email or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify({"erro": "E-mail inválido ou ausente"}), 400

    nome      = (dados.get("nome") or "").strip() or None
    ticket_id = (dados.get("ticket_id") or "").strip()
    if not ticket_id:
        ticket_id = f"MANUAL-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    if esta_em_processamento(email):
        return jsonify({
            "erro": f"Já existe um backup em andamento para {email}",
            "status": "ja_em_processamento",
        }), 409

    iniciar_backup_async(email, ticket_id, nome)
    logger.info(f"Backup manual iniciado — E-mail: {email}, Ticket: {ticket_id}, Nome: {nome}")

    return jsonify({
        "status": "iniciado",
        "email":     email,
        "ticket_id": ticket_id,
        "nome":      nome,
    }), 200
