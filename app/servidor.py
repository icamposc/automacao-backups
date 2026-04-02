"""
============================================================
Módulo do Servidor Flask — Automação de Backups
============================================================
Versão: 2.0.0
Data: 2026-04-02
Descrição: Servidor web Flask que expõe as rotas do sistema:
           - POST /webhook/backup-desligado
           - GET  /saude       (health check básico — compatibilidade)
           - GET  /health      (health check detalhado)
           - GET  /dashboard   (painel web)
============================================================
Histórico:
  2.0.0 (2026-04-02) — /health detalhado, inicialização do banco,
                        marcação de backups interrompidos
  1.0.0 (2026-02-19) — Versão inicial
============================================================
"""

from datetime import datetime

from flask import Flask, request, jsonify

from app.webhook_handler import validar_segredo_webhook, extrair_dados_webhook
from app.dashboard import bp as dashboard_bp
from processamento.orquestrador import iniciar_backup_async, esta_em_processamento
from processamento.limpeza import limpar_logs_antigos
from dados.banco import inicializar_banco, marcar_backups_interrompidos
from utils.logger import obter_logger

logger = obter_logger("servidor")

# Cria a aplicação Flask
app = Flask(__name__)

# Registra o Blueprint do Dashboard
app.register_blueprint(dashboard_bp)

# ── Inicialização na carga do módulo ───────────────────────────────────────
inicializar_banco()
marcar_backups_interrompidos()
limpar_logs_antigos()


@app.route("/webhook/backup-desligado", methods=["POST"])
def receber_webhook():
    """
    Recebe o webhook do Jira Service Management.

    1. Valida a assinatura HMAC
    2. Extrai e valida dados do payload
    3. Verifica se já existe processamento ativo para o e-mail
    4. Enfileira o backup no Celery
    5. Retorna HTTP 200 imediatamente
    """
    logger.info("Webhook recebido em /webhook/backup-desligado")

    try:
        assinatura = request.headers.get("X-Hub-Signature", "")
        dados_brutos = request.get_data()

        if not validar_segredo_webhook(dados_brutos, assinatura):
            logger.warning("Requisição rejeitada: assinatura inválida")
            return jsonify({"erro": "Assinatura do webhook inválida"}), 401

        payload = request.get_json(silent=True)
        if not payload:
            return jsonify({"erro": "Corpo da requisição deve ser JSON válido"}), 400

        dados = extrair_dados_webhook(payload)
        if not dados:
            return jsonify({"erro": "Payload inválido — campos obrigatórios ausentes"}), 400

        email     = dados["email"]
        ticket_id = dados["ticket_id"]
        nome      = dados.get("nome")

        if esta_em_processamento(email):
            logger.warning(f"Webhook duplicado ignorado — backup já em andamento para: {email}")
            return jsonify({
                "status": "ja_em_processamento",
                "mensagem": f"Backup já está em andamento para {email}",
            }), 200

        logger.info(f"Iniciando backup para: {email} (Ticket: {ticket_id})")
        iniciar_backup_async(email, ticket_id, nome)

        return jsonify({
            "status": "iniciado",
            "mensagem": f"Backup enfileirado para {email}",
            "ticket": ticket_id,
        }), 200

    except Exception as erro:
        logger.error(f"Erro interno ao processar webhook: {erro}", exc_info=True)
        return jsonify({"erro": "Erro interno do servidor"}), 500


@app.route("/saude", methods=["GET"])
def health_check_simples():
    """Health check básico — mantido para compatibilidade com ferramentas existentes."""
    return jsonify({
        "status": "ok",
        "servico": "automacao-backups",
        "versao": "2.0.0",
    }), 200


@app.route("/health", methods=["GET"])
def health_check():
    """
    Health check detalhado com status de todos os componentes.

    Resposta inclui:
    - status dos componentes (banco, redis/celery, servidor)
    - backups em andamento
    - última execução registrada
    - contadores gerais
    """
    from processamento.rastreador import obter_backups_ativos, obter_resumo, obter_historico
    from config.configuracoes import REDIS_URL

    componentes = {"servidor": "ok", "banco": "ok", "celery": "desconhecido"}
    status_geral = "ok"

    # Verifica o banco de dados
    try:
        resumo = obter_resumo()
        componentes["banco"] = "ok"
    except Exception as erro:
        componentes["banco"] = f"erro: {erro}"
        resumo = {"ativos": 0, "total_finalizados": 0, "sucessos": 0, "erros": 0}
        status_geral = "degradado"

    # Verifica Redis / Celery
    try:
        import redis
        r = redis.from_url(REDIS_URL, socket_connect_timeout=2)
        r.ping()
        componentes["celery"] = "ok"
    except Exception:
        componentes["celery"] = "indisponivel"
        status_geral = "degradado"

    # Última execução
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

    return jsonify({
        "status":               status_geral,
        "versao":               "2.0.0",
        "timestamp":            datetime.now().isoformat(),
        "componentes":          componentes,
        "backups_em_andamento": resumo.get("ativos", 0),
        "resumo":               resumo,
        "ultima_execucao":      ultima,
    }), 200


@app.route("/", methods=["GET"])
def raiz():
    return jsonify({
        "servico": "Automação de Backups",
        "versao": "2.0.0",
        "rotas": {
            "webhook":     "POST /webhook/backup-desligado",
            "health":      "GET /health",
            "health_v1":   "GET /saude",
            "dashboard":   "GET /dashboard",
            "api_ativos":  "GET /api/backups/ativos",
            "api_historico": "GET /api/backups/historico",
            "api_resumo":  "GET /api/backups/resumo",
        },
    }), 200


if __name__ == "__main__":
    from config.configuracoes import SERVIDOR_HOST, SERVIDOR_PORTA
    logger.info(f"Iniciando servidor em {SERVIDOR_HOST}:{SERVIDOR_PORTA}")
    app.run(host=SERVIDOR_HOST, port=SERVIDOR_PORTA, debug=True)
