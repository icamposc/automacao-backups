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

import csv
import io
import re
from datetime import datetime

from flask import Blueprint, Response, jsonify, render_template, request

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


@bp.route("/api/backups/fila")
def api_fila():
    """
    Retorna contagens vivas da fila de backups:
    - ativos:          backups em execução agora (banco)
    - limite_paralelo: teto do Celery worker (--concurrency)
    - aguardando:      tarefas pendentes na fila Redis do Celery
    """
    resumo = obter_resumo()
    ativos = resumo.get("ativos", 0)

    aguardando = 0
    try:
        import redis
        from config.configuracoes import REDIS_URL
        r = redis.from_url(REDIS_URL, socket_connect_timeout=2)
        aguardando = r.llen("celery")
    except Exception as erro:
        logger.warning(f"Falha ao consultar fila Redis: {erro}")

    return jsonify({
        "ativos":          ativos,
        "limite_paralelo": _LIMITE_PARALELO,
        "aguardando":      aguardando,
    })


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

    nome          = (dados.get("nome") or "").strip() or None
    ticket_id     = (dados.get("ticket_id") or "").strip()
    deletar_conta = bool(dados.get("deletar_conta", True))

    if not ticket_id:
        ticket_id = f"MANUAL-{datetime.now().strftime('%Y%m%d%H%M%S')}"

    if esta_em_processamento(email):
        return jsonify({
            "erro": f"Já existe um backup em andamento para {email}",
            "status": "ja_em_processamento",
        }), 409

    iniciar_backup_async(email, ticket_id, nome, deletar_conta=deletar_conta)
    logger.info(
        f"Backup manual iniciado — E-mail: {email}, Ticket: {ticket_id}, "
        f"Nome: {nome}, Deletar conta: {deletar_conta}"
    )

    return jsonify({
        "status":        "iniciado",
        "email":         email,
        "ticket_id":     ticket_id,
        "nome":          nome,
        "deletar_conta": deletar_conta,
    }), 200


# Template servido para download — mínimo, apenas cabeçalho.
# A dica textual no dashboard já explica o formato e o limite.
_TEMPLATE_CSV_LOTE = "email\n"


@bp.route("/api/backups/lote/template")
def api_lote_template():
    """Serve um arquivo CSV de exemplo para download (modelo de upload em massa)."""
    return Response(
        _TEMPLATE_CSV_LOTE,
        mimetype="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="template-backup-massa.csv"',
            "Cache-Control": "no-store",
        },
    )


# Regex de validação de e-mail (mesmo padrão usado em /api/backups/iniciar)
_REGEX_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Limite por upload — alinhado com a vazão prática do worker
# (Celery --concurrency=4) e o limite do Google Vault (18 exports simultâneos).
# Acima disso o usuário deve dividir o lote em arquivos menores.
_MAX_EMAILS_POR_LOTE = 50

# Reflete --concurrency do Celery em docker-compose.yml:125.
# Se aumentar lá, atualizar aqui (ou expor via env var).
_LIMITE_PARALELO = 4


def _extrair_emails_csv(conteudo: str) -> list[str]:
    """
    Extrai e-mails de um conteúdo CSV.

    Aceita dois formatos:
    - CSV com cabeçalho contendo coluna 'email' (case-insensitive)
    - CSV de coluna única sem cabeçalho (uma linha por e-mail)

    Linhas em branco, comentários (#) e espaços são ignorados.
    Preserva a ordem e remove duplicatas mantendo a 1ª ocorrência.
    """
    leitor = csv.reader(io.StringIO(conteudo))
    linhas = [linha for linha in leitor if any((c or "").strip() for c in linha)]

    # Remove linhas de comentário no topo (#) — permite que o template tenha
    # instruções inline antes do cabeçalho 'email'.
    while linhas and (linhas[0][0] or "").strip().startswith("#"):
        linhas.pop(0)

    if not linhas:
        return []

    # Detecta cabeçalho 'email'
    primeira = [(c or "").strip().lower() for c in linhas[0]]
    if "email" in primeira:
        idx_email = primeira.index("email")
        linhas_dados = linhas[1:]
    else:
        idx_email = 0
        linhas_dados = linhas

    emails: list[str] = []
    vistos: set[str] = set()
    for linha in linhas_dados:
        if idx_email >= len(linha):
            continue
        valor = (linha[idx_email] or "").strip().lower()
        if not valor or valor.startswith("#"):
            continue
        if valor in vistos:
            continue
        vistos.add(valor)
        emails.append(valor)
    return emails


@bp.route("/api/backups/lote", methods=["POST"])
def api_iniciar_lote():
    """
    Recebe um arquivo .csv com lista de e-mails e enfileira backups em massa.

    Form-data:
        arquivo       (obrigatório): arquivo .csv (UTF-8). Aceita coluna 'email'
                                     com cabeçalho ou uma coluna única sem cabeçalho.
        deletar_conta (opcional):    "true"/"false" (padrão "true"). Aplicado a
                                     todos os e-mails do lote.

    Retorna sumário: aceitos, já em processamento, inválidos, duplicados (no arquivo).
    """
    arquivo = request.files.get("arquivo")
    if not arquivo or not arquivo.filename:
        return jsonify({"erro": "Arquivo .csv ausente (campo 'arquivo')"}), 400

    if not arquivo.filename.lower().endswith(".csv"):
        return jsonify({"erro": "Apenas arquivos .csv são aceitos"}), 400

    # Decodifica forçando UTF-8 com fallback latin-1 (planilhas exportadas no Windows)
    bytes_arquivo = arquivo.read()
    try:
        conteudo = bytes_arquivo.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            conteudo = bytes_arquivo.decode("latin-1")
        except Exception:
            return jsonify({"erro": "Não foi possível decodificar o CSV (use UTF-8)"}), 400

    deletar_conta_raw = (request.form.get("deletar_conta") or "true").strip().lower()
    deletar_conta = deletar_conta_raw in ("true", "1", "sim", "on", "yes")

    emails_brutos = _extrair_emails_csv(conteudo)

    if not emails_brutos:
        return jsonify({"erro": "Nenhum e-mail encontrado no arquivo"}), 400

    if len(emails_brutos) > _MAX_EMAILS_POR_LOTE:
        return jsonify({
            "erro": (
                f"Lote excede o limite de {_MAX_EMAILS_POR_LOTE} e-mails por upload "
                f"(recebido: {len(emails_brutos)}). Divida em arquivos menores e "
                f"envie em sequência — os backups serão enfileirados na ordem."
            )
        }), 400

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    aceitos: list[dict] = []
    ja_em_processamento: list[str] = []
    invalidos: list[str] = []

    for indice, email in enumerate(emails_brutos, start=1):
        if not _REGEX_EMAIL.match(email):
            invalidos.append(email)
            continue
        if esta_em_processamento(email):
            ja_em_processamento.append(email)
            continue
        ticket_id = f"LOTE-{timestamp}-{indice:03d}"
        try:
            iniciar_backup_async(email, ticket_id, None, deletar_conta=deletar_conta)
            aceitos.append({"email": email, "ticket_id": ticket_id})
        except Exception as erro:
            logger.error(f"Falha ao enfileirar backup em lote para {email}: {erro}")
            invalidos.append(email)

    logger.info(
        f"Lote CSV processado — arquivo: {arquivo.filename}, "
        f"aceitos: {len(aceitos)}, em_processamento: {len(ja_em_processamento)}, "
        f"invalidos: {len(invalidos)}, deletar_conta: {deletar_conta}"
    )

    return jsonify({
        "status": "concluido",
        "deletar_conta": deletar_conta,
        "total_linhas":  len(emails_brutos),
        "aceitos":              aceitos,
        "ja_em_processamento":  ja_em_processamento,
        "invalidos":            invalidos,
    }), 200
