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

import os
import threading
from datetime import timedelta

from flask import Flask, request, jsonify

from app.webhook_handler import validar_segredo_webhook, extrair_dados_webhook
from app.dashboard import bp as dashboard_bp
from app.autenticacao import bp as auth_bp, exigir_login, usuario_logado
from config.configuracoes import (
    FLASK_SECRET_KEY,
    SESSION_COOKIE_SECURE,
    SESSION_HORAS,
)
from processamento.orquestrador import iniciar_backup_async, esta_em_processamento
from dados.repositorio_backups import existe_backup_concluido_por_ticket
from processamento.limpeza import limpar_logs_antigos, limpar_zips_sincronizados
from dados.banco import inicializar_banco
from processamento.recuperacao import recuperar_backups_interrompidos
from processamento.saude import coletar_status_saude, iniciar_monitor_saude
from processamento.finalizacao_nas import iniciar_monitor_finalizacao_nas
from utils.logger import obter_logger

logger = obter_logger("servidor")

# Cria a aplicação Flask
app = Flask(__name__)

# Limite de tamanho do payload (protege contra requisições maliciosas gigantes)
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB

# ── Sessão / login ─────────────────────────────────────────────────────────
# A chave deve ser fixa em produção (Gunicorn --workers 2 compartilham a
# mesma sessão). Sem FLASK_SECRET_KEY no .env, gera uma aleatória apenas para
# dev/teste — nesse caso as sessões não sobrevivem a reinício nem são
# compartilhadas entre workers.
if FLASK_SECRET_KEY:
    app.secret_key = FLASK_SECRET_KEY
else:
    app.secret_key = os.urandom(32)
    logger.warning(
        "FLASK_SECRET_KEY não definida — usando chave aleatória temporária. "
        "Defina FLASK_SECRET_KEY no .env para produção (Gunicorn com 2 workers)."
    )

app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=SESSION_COOKIE_SECURE,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=SESSION_HORAS),
)

# Registra os Blueprints (login + dashboard) e o guard de proteção.
# O guard roda antes de cada requisição e exige login para as rotas do
# blueprint 'dashboard' (/dashboard e /api/backups/*). Webhook (HMAC),
# /health e a tela de login permanecem abertos.
app.register_blueprint(auth_bp)
app.register_blueprint(dashboard_bp)
app.before_request(exigir_login)

# ── Inicialização na carga do módulo ───────────────────────────────────────
# banco e recuperação são síncronos (necessários antes da 1ª requisição)
inicializar_banco()
recuperar_backups_interrompidos()

# limpeza de logs pode demorar — executa em background para não atrasar o startup
threading.Thread(target=limpar_logs_antigos, daemon=True).start()

# safety-net: apaga ZIPs orfaos em sync_nas com mais de NAS_SYNC_RETENCAO_HORAS
# (a exclusao normal acontece na finalizacao_nas, apos a janela de espera)
threading.Thread(target=limpar_zips_sincronizados, daemon=True).start()

# Monitor de saúde — thread daemon que alerta no Google Chat de LOGS quando
# o sistema entra/sai do estado degradado (componentes, disco, backups stuck).
iniciar_monitor_saude()

# Monitor de finalizacao NAS — thread daemon que, a cada 30 min, varre backups
# em status 'aguardando_nas' ha mais de NAS_SYNC_HORAS_ESPERA (6h) e fecha o
# ciclo (Jira + conta + chat) e apaga o ZIP local.
iniciar_monitor_finalizacao_nas()


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

    Delega para `processamento.saude.coletar_status_saude`, que é o mesmo
    coletor usado pelo monitor periódico — garante que /health e os
    alertas de Chat de LOGS observem o estado por uma única fonte da verdade.

    Status HTTP:
    - 200 quando status_geral == "ok"
    - 503 quando degradado (essencial para o healthcheck do Docker)

    Corpo da resposta:
    - Sem sessão (público): apenas o mínimo — status/versão/timestamp. NÃO
      expõe componentes, contadores nem PII (e-mails de colaboradores,
      tickets), evitando que o /health vire um vazamento de dados do painel.
    - Com sessão de admin: payload completo de diagnóstico.

    O healthcheck do Docker usa apenas o código HTTP (200/503), então o corpo
    mínimo não o afeta.
    """
    status_geral, payload = coletar_status_saude()
    http_status = 200 if status_geral == "ok" else 503

    if usuario_logado():
        corpo = payload
    else:
        corpo = {
            "status":    status_geral,
            "versao":    payload.get("versao"),
            "timestamp": payload.get("timestamp"),
        }
    return jsonify(corpo), http_status


@app.route("/", methods=["GET"])
def raiz():
    return jsonify({
        "servico": "Automação de Backups",
        "versao": "2.0.0",
        "rotas": {
            "webhook":     "POST /webhook/backup-desligado",
            "health":      "GET /health",
            "health_v1":   "GET /saude",
            "login":       "GET/POST /login",
            "logout":      "GET /logout",
            "dashboard":   "GET /dashboard",
            "api_ativos":  "GET /api/backups/ativos",
            "api_historico": "GET /api/backups/historico",
            "api_resumo":  "GET /api/backups/resumo",
            "api_lote":    "POST /api/backups/lote",
            "api_lote_template": "GET /api/backups/lote/template",
            "api_fila":    "GET /api/backups/fila",
        },
    }), 200


if __name__ == "__main__":
    from config.configuracoes import SERVIDOR_HOST, SERVIDOR_PORTA
    logger.info(f"Iniciando servidor em {SERVIDOR_HOST}:{SERVIDOR_PORTA}")
    app.run(host=SERVIDOR_HOST, port=SERVIDOR_PORTA, debug=False)
