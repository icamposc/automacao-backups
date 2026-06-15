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
from config.configuracoes import LIMITE_PARALELO_BACKUPS
from utils.logger import obter_logger
from utils import auditoria

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
    aguardando_nas = resumo.get("aguardando_nas", 0)

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
        "aguardando_nas":  aguardando_nas,
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
        auditoria.registrar("backup_manual", resultado="ja_em_processamento", email=email)
        return jsonify({
            "erro": f"Já existe um backup em andamento para {email}",
            "status": "ja_em_processamento",
        }), 409

    iniciar_backup_async(email, ticket_id, nome, deletar_conta=deletar_conta)
    logger.info(
        f"Backup manual iniciado — E-mail: {email}, Ticket: {ticket_id}, "
        f"Nome: {nome}, Deletar conta: {deletar_conta}"
    )
    auditoria.registrar(
        "backup_manual", resultado="iniciado",
        email=email, ticket=ticket_id, deletar_conta=deletar_conta,
    )

    return jsonify({
        "status":        "iniciado",
        "email":         email,
        "ticket_id":     ticket_id,
        "nome":          nome,
        "deletar_conta": deletar_conta,
    }), 200


@bp.route("/api/backups/refazer", methods=["POST"])
def api_refazer():
    """
    Reenfileira em lote backups que falharam (botão "Refazer Selecionados").

    Body JSON:
        backups: lista de objetos { email, nome?, ticket_id? }

    A exclusão da conta Workspace NUNCA é feita nesta retentativa
    (deletar_conta=False), conforme o aviso exibido no dashboard.

    Retorna 200 com { resultados: [{ email, status }] }, onde status é:
        'iniciado'            — reenfileirado com sucesso
        'ja_em_processamento' — já existe backup ativo para o e-mail
        'erro'                — e-mail inválido ou falha ao enfileirar
    """
    dados = request.get_json(silent=True) or {}
    backups = dados.get("backups")
    if not isinstance(backups, list) or not backups:
        return jsonify({"erro": "Lista de backups vazia ou ausente"}), 400

    resultados = []
    for item in backups:
        email = ((item or {}).get("email") or "").strip().lower()
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            resultados.append({"email": email, "status": "erro", "motivo": "e-mail inválido"})
            continue

        nome      = (item.get("nome") or "").strip() or None
        ticket_id = (item.get("ticket_id") or "").strip()
        if not ticket_id:
            ticket_id = f"REFAZER-{datetime.now().strftime('%Y%m%d%H%M%S')}"

        if esta_em_processamento(email):
            resultados.append({"email": email, "status": "ja_em_processamento"})
            continue

        try:
            # deletar_conta=False: retentativa nunca exclui a conta Workspace.
            iniciar_backup_async(email, ticket_id, nome, deletar_conta=False)
            logger.info(f"Backup reenfileirado (refazer) — E-mail: {email}, Ticket: {ticket_id}")
            resultados.append({"email": email, "status": "iniciado", "ticket_id": ticket_id})
        except Exception as erro:
            logger.error(f"Falha ao reenfileirar (refazer) {email}: {erro}")
            resultados.append({"email": email, "status": "erro", "motivo": str(erro)})

    iniciados = [r["email"] for r in resultados if r["status"] == "iniciado"]
    auditoria.registrar(
        "backup_refazer", resultado="ok",
        total=len(resultados), reenfileirados=len(iniciados),
        emails=",".join(iniciados) if iniciados else "-",
    )
    return jsonify({"resultados": resultados}), 200


# Template servido para download — apenas o cabeçalho.
# 'email' é obrigatório; 'nome' e 'chamado' são opcionais (quando preenchidos,
# o backup age no chamado real informado — senão, segue como lote anônimo).
_TEMPLATE_CSV_LOTE = "email,nome,chamado\n"


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
# (Celery --concurrency=9) e o limite do Google Vault (18 exports simultâneos).
# Acima disso o usuário deve dividir o lote em arquivos menores.
_MAX_EMAILS_POR_LOTE = 50

# Reflete o --concurrency do worker Celery (deploy/docker-compose.yml).
# Lido de config (env LIMITE_PARALELO_BACKUPS, default 9) para não divergir.
_LIMITE_PARALELO = LIMITE_PARALELO_BACKUPS


# Aliases de cabeçalho aceitos (normalizados: minúsculo, sem espaços nas pontas).
_COLUNAS_EMAIL   = {"email", "e-mail", "e_mail"}
_COLUNAS_NOME    = {"nome", "name", "colaborador"}
_COLUNAS_CHAMADO = {"chamado", "ticket", "ticket_id", "ticketid",
                    "numero", "número", "numero do chamado", "número do chamado",
                    "nº chamado"}


def _extrair_registros_csv(conteudo: str) -> list[dict]:
    """
    Extrai registros de um conteúdo CSV: e-mail obrigatório, nome e chamado opcionais.

    Aceita dois formatos:
    - CSV com cabeçalho contendo a coluna 'email' (case-insensitive). Quando
      presentes, as colunas 'nome' e 'chamado' (com aliases) também são lidas.
    - CSV de coluna única sem cabeçalho (uma linha por e-mail) — compatibilidade
      com o formato antigo; nome e chamado ficam vazios.

    Linhas em branco, comentários (#) e espaços são ignorados.
    Preserva a ordem e remove duplicatas por e-mail mantendo a 1ª ocorrência.

    Returns:
        Lista de dicts {"email": str, "nome": str|None, "ticket_id": str|None}.
        Quando 'chamado' não é informado, ticket_id vem None (o chamador então
        gera o identificador sintético LOTE-*, como no comportamento atual).
    """
    leitor = csv.reader(io.StringIO(conteudo))
    linhas = [linha for linha in leitor if any((c or "").strip() for c in linha)]

    # Remove linhas de comentário no topo (#) — permite que o template tenha
    # instruções inline antes do cabeçalho.
    while linhas and (linhas[0][0] or "").strip().startswith("#"):
        linhas.pop(0)

    if not linhas:
        return []

    # Detecta cabeçalho pela presença de uma coluna de e-mail conhecida.
    cabecalho = [(c or "").strip().lower() for c in linhas[0]]
    if any(c in _COLUNAS_EMAIL for c in cabecalho):
        idx_email   = next(i for i, c in enumerate(cabecalho) if c in _COLUNAS_EMAIL)
        idx_nome    = next((i for i, c in enumerate(cabecalho) if c in _COLUNAS_NOME), None)
        idx_chamado = next((i for i, c in enumerate(cabecalho) if c in _COLUNAS_CHAMADO), None)
        linhas_dados = linhas[1:]
    else:
        # Sem cabeçalho reconhecido: coluna única = e-mail (formato antigo).
        idx_email, idx_nome, idx_chamado = 0, None, None
        linhas_dados = linhas

    def _celula(linha: list, idx) -> str:
        if idx is None or idx >= len(linha):
            return ""
        return (linha[idx] or "").strip()

    registros: list[dict] = []
    vistos: set[str] = set()
    for linha in linhas_dados:
        email = _celula(linha, idx_email).lower()
        if not email or email.startswith("#"):
            continue
        if email in vistos:
            continue
        vistos.add(email)
        nome = _celula(linha, idx_nome) or None
        ticket = _celula(linha, idx_chamado).upper() or None
        registros.append({"email": email, "nome": nome, "ticket_id": ticket})
    return registros


@bp.route("/api/backups/lote", methods=["POST"])
def api_iniciar_lote():
    """
    Recebe um arquivo .csv com lista de e-mails e enfileira backups em massa.

    Form-data:
        arquivo       (obrigatório): arquivo .csv (UTF-8). Aceita coluna 'email'
                                     com cabeçalho ou uma coluna única sem cabeçalho.
                                     Colunas opcionais 'nome' e 'chamado': quando
                                     preenchidas, o backup age no chamado real
                                     informado (comentário, formulários, transição
                                     para Resolvido). Sem 'chamado', mantém o
                                     comportamento de lote (ticket sintético LOTE-*).
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

    registros = _extrair_registros_csv(conteudo)

    if not registros:
        return jsonify({"erro": "Nenhum e-mail encontrado no arquivo"}), 400

    if len(registros) > _MAX_EMAILS_POR_LOTE:
        return jsonify({
            "erro": (
                f"Lote excede o limite de {_MAX_EMAILS_POR_LOTE} e-mails por upload "
                f"(recebido: {len(registros)}). Divida em arquivos menores e "
                f"envie em sequência — os backups serão enfileirados na ordem."
            )
        }), 400

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    aceitos: list[dict] = []
    ja_em_processamento: list[str] = []
    invalidos: list[str] = []

    for indice, registro in enumerate(registros, start=1):
        email = registro["email"]
        if not _REGEX_EMAIL.match(email):
            invalidos.append(email)
            continue
        if esta_em_processamento(email):
            ja_em_processamento.append(email)
            continue
        # Usa o chamado real informado no CSV; sem ele, mantém o ticket
        # sintético LOTE-* (comportamento anterior). O nome segue o mesmo
        # critério: o que veio no CSV, ou None.
        ticket_id = registro["ticket_id"] or f"LOTE-{timestamp}-{indice:03d}"
        try:
            iniciar_backup_async(email, ticket_id, registro["nome"], deletar_conta=deletar_conta)
            aceitos.append({"email": email, "ticket_id": ticket_id})
        except Exception as erro:
            logger.error(f"Falha ao enfileirar backup em lote para {email}: {erro}")
            invalidos.append(email)

    logger.info(
        f"Lote CSV processado — arquivo: {arquivo.filename}, "
        f"aceitos: {len(aceitos)}, em_processamento: {len(ja_em_processamento)}, "
        f"invalidos: {len(invalidos)}, deletar_conta: {deletar_conta}"
    )
    auditoria.registrar(
        "backup_lote", resultado="ok",
        arquivo=arquivo.filename, total_linhas=len(registros),
        aceitos=len(aceitos), ja_em_processamento=len(ja_em_processamento),
        invalidos=len(invalidos), deletar_conta=deletar_conta,
    )

    return jsonify({
        "status": "concluido",
        "deletar_conta": deletar_conta,
        "total_linhas":  len(registros),
        "aceitos":              aceitos,
        "ja_em_processamento":  ja_em_processamento,
        "invalidos":            invalidos,
    }), 200
