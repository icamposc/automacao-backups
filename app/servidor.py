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

import threading
from datetime import datetime

from flask import Flask, request, jsonify

from app.webhook_handler import validar_segredo_webhook, extrair_dados_webhook
from app.dashboard import bp as dashboard_bp
from processamento.orquestrador import iniciar_backup_async, esta_em_processamento
from dados.repositorio_backups import existe_backup_concluido_por_ticket
from processamento.limpeza import limpar_logs_antigos
from dados.banco import inicializar_banco
from processamento.recuperacao import recuperar_backups_interrompidos
from utils.logger import obter_logger

logger = obter_logger("servidor")

# Cria a aplicação Flask
app = Flask(__name__)

# Limite de tamanho do payload (protege contra requisições maliciosas gigantes)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB

# Registra o Blueprint do Dashboard
app.register_blueprint(dashboard_bp)

# ── Inicialização na carga do módulo ───────────────────────────────────────
# banco e recuperação são síncronos (necessários antes da 1ª requisição)
inicializar_banco()
recuperar_backups_interrompidos()

# limpeza de logs pode demorar — executa em background para não atrasar o startup
threading.Thread(target=limpar_logs_antigos, daemon=True).start()


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
            logger.warning(f"Webhook ignorado — backup já em andamento: {email}")
            return jsonify({
                "status": "ja_em_processamento",
                "mensagem": f"Backup já está em andamento para {email}",
            }), 200

        if existe_backup_concluido_por_ticket(ticket_id):
            logger.warning(f"Webhook ignorado — backup já concluído para ticket: {ticket_id}")
            return jsonify({
                "status": "ja_concluido",
                "mensagem": f"Ticket {ticket_id} já possui backup concluído com sucesso",
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


# Threshold para considerar o disco em estado degradado.
# /mnt/hdd é o volume crítico — abaixo de 20% livres, qualquer backup
# grande novo arrisca encher o disco e travar o sistema.
_DISCO_MIN_LIVRE_PCT = 20

# Limite acima do qual um backup em_andamento é considerado "stuck"
# (worker travado, D-state, deadlock de FS, ou export do Vault muito
# grande). Acima dele, /health entra em "degradado".
_STUCK_HORAS = 12


@app.route("/saude", methods=["GET"])
def health_check_simples():
    """Alias legado para /health. Mantido para ferramentas externas que
    apontavam para /saude antes da v2.1. Retorna o mesmo conteúdo e
    status HTTP do /health detalhado.
    """
    return health_check()


@app.route("/health", methods=["GET"])
def health_check():
    """
    Health check detalhado com status de todos os componentes.

    Resposta inclui:
    - banco SQLite (read query)
    - Redis (ping)
    - Celery (inspect ping com timeout 5s — detecta worker em D-state)
    - disco em PASTA_VAULT (% livre, alerta abaixo do threshold)
    - backups travados (status='em_andamento' há mais de _STUCK_HORAS horas)
    - última execução

    Status HTTP:
    - 200 quando tudo está ok
    - 503 quando qualquer componente está degradado ou indisponível
      (essencial para healthcheck do Docker considerar o container unhealthy)
    """
    import shutil
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

    # ── Banco ──────────────────────────────────────────────────────────
    try:
        resumo = obter_resumo()
        componentes["banco"] = "ok"
    except Exception as erro:
        componentes["banco"] = f"erro: {erro}"
        resumo = {"ativos": 0, "total_finalizados": 0, "sucessos": 0, "erros": 0}
        status_geral = "degradado"

    # ── Redis (broker do Celery) ───────────────────────────────────────
    try:
        import redis
        r = redis.from_url(REDIS_URL, socket_connect_timeout=2)
        r.ping()
        componentes["redis"] = "ok"
    except Exception as erro:
        componentes["redis"] = f"indisponivel: {erro}"
        status_geral = "degradado"

    # ── Celery (worker responde a inspect ping?) ───────────────────────
    # Diferencia "Redis up mas worker travado em D-state" de "tudo bem".
    # O ping volta um dict {hostname: {ok: pong}} por worker ativo.
    try:
        from worker.celery_app import app as celery_app
        pongs = celery_app.control.inspect(timeout=5).ping()
        if pongs:
            componentes["celery"] = f"ok ({len(pongs)} worker(s))"
        else:
            componentes["celery"] = "sem_workers"
            status_geral = "degradado"
    except Exception as erro:
        componentes["celery"] = f"erro: {erro}"
        status_geral = "degradado"

    # ── Disco em PASTA_VAULT ───────────────────────────────────────────
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

    # ── Backups stuck (em_andamento há > N horas) ──────────────────────
    stuck = []
    try:
        stuck = listar_backups_stuck(horas=_STUCK_HORAS)
        if stuck:
            status_geral = "degradado"
    except Exception as erro:
        logger.warning(f"Falha ao listar backups stuck: {erro}")

    # ── Última execução ────────────────────────────────────────────────
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

    http_status = 200 if status_geral == "ok" else 503

    return jsonify({
        "status":               status_geral,
        "versao":               "2.0.0",
        "timestamp":            datetime.now().isoformat(),
        "componentes":          componentes,
        "backups_em_andamento": resumo.get("ativos", 0),
        "backups_stuck":        stuck,
        "resumo":               resumo,
        "ultima_execucao":      ultima,
    }), http_status


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
    app.run(host=SERVIDOR_HOST, port=SERVIDOR_PORTA, debug=False)
