"""
============================================================
Módulo do Servidor Flask — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-02-19
Descrição: Servidor web Flask que expõe as rotas do sistema:
           - POST /webhook/backup-desligado → Recebe webhook do Jira
           - GET /saude → Health check para monitoramento

           O servidor recebe requisições do webhook, valida o
           payload, e inicia o processamento em background,
           retornando HTTP 200 imediatamente.
============================================================
Histórico:
  1.0.0 (2026-02-19) — Versão inicial
============================================================
"""

from flask import Flask, request, jsonify

from app.webhook_handler import validar_segredo_webhook, extrair_dados_webhook
from processamento.orquestrador import iniciar_backup_async, esta_em_processamento
from utils.logger import obter_logger

logger = obter_logger("servidor")

# Cria a aplicação Flask
app = Flask(__name__)


@app.route("/webhook/backup-desligado", methods=["POST"])
def receber_webhook():
    """
    Rota que recebe o webhook do Jira Service Management.

    Fluxo:
    1. Valida a assinatura do webhook (se configurada)
    2. Extrai e valida os dados do payload (e-mail, ticket ID)
    3. Verifica se já existe processamento ativo para o mesmo e-mail
    4. Inicia o backup em uma thread de background
    5. Retorna HTTP 200 imediatamente

    Headers esperados:
    - Content-Type: application/json
    - X-Hub-Signature (opcional): Assinatura HMAC do webhook

    Corpo esperado (JSON):
    {
        "email_colaborador": "usuario@empresa.com",
        "ticket_id": "SPN-123",
        "nome_colaborador": "Nome do Colaborador"  (opcional)
    }

    Respostas:
    - 200: Backup iniciado com sucesso (ou já em processamento)
    - 400: Payload inválido
    - 401: Assinatura do webhook inválida
    - 500: Erro interno do servidor
    """
    logger.info("Webhook recebido em /webhook/backup-desligado")

    try:
        # --- Validação da assinatura do webhook ---
        assinatura = request.headers.get("X-Hub-Signature", "")
        dados_brutos = request.get_data()

        if not validar_segredo_webhook(dados_brutos, assinatura):
            logger.warning("Requisição rejeitada: assinatura inválida")
            return jsonify({"erro": "Assinatura do webhook inválida"}), 401

        # --- Extração e validação do payload ---
        payload = request.get_json(silent=True)

        if not payload:
            logger.warning("Requisição sem corpo JSON válido")
            return jsonify({"erro": "Corpo da requisição deve ser JSON válido"}), 400

        dados = extrair_dados_webhook(payload)

        if not dados:
            logger.warning("Payload do webhook inválido")
            return jsonify({"erro": "Payload inválido — campos obrigatórios ausentes"}), 400

        email = dados["email"]
        ticket_id = dados["ticket_id"]
        nome = dados.get("nome")

        # --- Verificação de duplicatas ---
        if esta_em_processamento(email):
            logger.warning(f"Webhook duplicado ignorado — backup já em andamento para: {email}")
            return jsonify({
                "status": "ja_em_processamento",
                "mensagem": f"Backup já está em andamento para {email}",
            }), 200

        # --- Inicia o backup em background ---
        logger.info(f"Iniciando backup para: {email} (Ticket: {ticket_id})")
        iniciar_backup_async(email, ticket_id, nome)

        return jsonify({
            "status": "iniciado",
            "mensagem": f"Backup iniciado para {email}",
            "ticket": ticket_id,
        }), 200

    except Exception as erro:
        logger.error(f"Erro interno ao processar webhook: {erro}", exc_info=True)
        return jsonify({"erro": "Erro interno do servidor"}), 500


@app.route("/saude", methods=["GET"])
def health_check():
    """
    Rota de health check para monitoramento do servidor.

    Retorna HTTP 200 com informações básicas do sistema.
    Pode ser usada por ferramentas como PRTG, Zabbix, etc.

    Respostas:
    - 200: Servidor funcionando normalmente
    """
    return jsonify({
        "status": "ok",
        "servico": "automacao-backups",
        "versao": "1.0.0",
    }), 200


@app.route("/", methods=["GET"])
def raiz():
    """
    Rota raiz — redireciona para informações básicas.
    """
    return jsonify({
        "servico": "Automação de Backups",
        "versao": "1.0.0",
        "rotas": {
            "webhook": "POST /webhook/backup-desligado",
            "health_check": "GET /saude",
        },
    }), 200


# Permite rodar diretamente com: python -m app.servidor
if __name__ == "__main__":
    from config.configuracoes import SERVIDOR_HOST, SERVIDOR_PORTA

    logger.info(f"Iniciando servidor em {SERVIDOR_HOST}:{SERVIDOR_PORTA}")
    app.run(host=SERVIDOR_HOST, port=SERVIDOR_PORTA, debug=True)
