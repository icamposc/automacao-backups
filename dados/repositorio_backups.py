"""
============================================================
Repositório de Backups — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-04-02
Descrição: Camada de acesso a dados para a tabela 'backups'
           e 'etapas_backup' no SQLite. Encapsula todas as
           operações CRUD e mantém a mesma interface esperada
           pelo rastreador.
============================================================
"""

import sqlite3
import time
from datetime import datetime, timezone
from typing import Optional

from dados.banco import obter_conexao
from utils.logger import obter_logger

logger = obter_logger("repositorio_backups")

# Debounce para atualizar_progresso_etapa: evita gravações excessivas no SQLite
# a cada chunk baixado/enviado. Persiste no máximo uma vez por janela de tempo,
# exceto quando o progresso atinge 100% (sempre persiste o valor final).
_DEBOUNCE_SEGUNDOS = 2
_ultimo_progresso: dict = {}  # (email, numero_etapa) → (monotonic_ts, pct)

# Definição das etapas (espelhada de rastreador para evitar importação circular)
_ETAPAS_PADRAO = [
    {"numero": 1, "nome": "Notificação Jira",    "descricao": "Notificando Jira sobre início do backup"},
    {"numero": 2, "nome": "Criar Exportações",   "descricao": "Criando exportações de E-mail e Drive no Vault"},
    {"numero": 3, "nome": "Monitorar Exportações","descricao": "Aguardando conclusão das exportações"},
    {"numero": 4, "nome": "Baixar Arquivos",     "descricao": "Baixando arquivos exportados do Cloud Storage"},
    {"numero": 5, "nome": "Compactar ZIP",       "descricao": "Compactando arquivos em ZIP"},
    {"numero": 6, "nome": "Upload Drive",        "descricao": "Enviando backup para Google Drive Compartilhado"},
    {"numero": 7, "nome": "Atualizar Jira",      "descricao": "Atualizando ticket Jira com resultado"},
    {"numero": 8, "nome": "Excluir Conta",       "descricao": "Verificando backup e excluindo conta"},
]


# ─────────────────────────────────────────────────────────────────────────────
# Escrita
# ─────────────────────────────────────────────────────────────────────────────

def inserir_backup(email: str, ticket_id: str, nome: str = None, deletar_conta: bool = True) -> int:
    """
    Insere um novo backup e suas 8 etapas. Retorna o ID gerado.

    Raises:
        ValueError: se já existir um backup em andamento para este e-mail
                    (violação do índice único parcial — race condition detectada).
    """
    conn = obter_conexao()
    try:
        cursor = conn.execute(
            """INSERT INTO backups (email, ticket_id, nome, status_geral, inicio, deletar_conta)
               VALUES (?, ?, ?, 'em_andamento', ?, ?)""",
            (email, ticket_id, nome or email, datetime.now(timezone.utc).isoformat(), int(deletar_conta)),
        )
    except sqlite3.IntegrityError:
        # Índice único parcial detectou tentativa duplicada (race condition entre webhooks)
        raise ValueError(
            f"Backup já em andamento para {email} — webhook duplicado ignorado"
        )

    backup_id = cursor.lastrowid

    conn.executemany(
        """INSERT INTO etapas_backup (backup_id, numero, nome, descricao, status)
           VALUES (?, ?, ?, ?, 'pendente')""",
        [(backup_id, e["numero"], e["nome"], e["descricao"]) for e in _ETAPAS_PADRAO],
    )
    conn.commit()
    logger.debug(f"Backup inserido: id={backup_id}, email={email}")
    return backup_id


def atualizar_etapa(email: str, numero_etapa: int, status: str) -> None:
    """Atualiza o status de uma etapa do backup ativo."""
    backup_id = _obter_id_ativo(email)
    if backup_id is None:
        logger.warning(f"Backup ativo não encontrado ao atualizar etapa: {email}")
        return

    agora = datetime.now(timezone.utc).isoformat()
    conn = obter_conexao()

    if status == "em_andamento":
        conn.execute(
            "UPDATE etapas_backup SET status = ?, inicio = ? WHERE backup_id = ? AND numero = ?",
            (status, agora, backup_id, numero_etapa),
        )
    else:
        conn.execute(
            "UPDATE etapas_backup SET status = ?, fim = ? WHERE backup_id = ? AND numero = ?",
            (status, agora, backup_id, numero_etapa),
        )
    conn.commit()


def finalizar_backup(
    email: str,
    sucesso: bool,
    erro_mensagem: str = None,
    link_drive: str = None,
    sha256_zip: str = None,
) -> None:
    """Marca o backup como concluído ou com erro."""
    backup_id = _obter_id_ativo(email)
    if backup_id is None:
        logger.warning(f"Backup ativo não encontrado ao finalizar: {email}")
        return

    conn = obter_conexao()
    conn.execute(
        """UPDATE backups
           SET status_geral = ?, fim = ?, erro_mensagem = ?, link_drive = ?, sha256_zip = ?
           WHERE id = ?""",
        (
            "concluido" if sucesso else "erro",
            datetime.now(timezone.utc).isoformat(),
            erro_mensagem,
            link_drive,
            sha256_zip,
            backup_id,
        ),
    )
    conn.commit()
    logger.debug(f"Backup finalizado: id={backup_id}, sucesso={sucesso}")


def atualizar_progresso_etapa(email: str, numero_etapa: int, pct: int) -> None:
    """
    Persiste o percentual de progresso de uma etapa em andamento.

    Aplica debounce: só escreve no SQLite se passaram pelo menos
    _DEBOUNCE_SEGUNDOS desde a última gravação, ou se o progresso atingiu 100%.
    Evita I/O excessivo durante downloads/uploads por chunk.
    """
    chave = (email, numero_etapa)
    agora = time.monotonic()
    ultimo = _ultimo_progresso.get(chave)
    if ultimo is not None and pct != 100 and (agora - ultimo[0]) < _DEBOUNCE_SEGUNDOS:
        return  # dentro da janela de debounce — descarta
    _ultimo_progresso[chave] = (agora, pct)

    backup_id = _obter_id_ativo(email)
    if backup_id is None:
        return
    conn = obter_conexao()
    conn.execute(
        "UPDATE etapas_backup SET progresso_pct = ? WHERE backup_id = ? AND numero = ?",
        (pct, backup_id, numero_etapa),
    )
    conn.commit()


def salvar_celery_task_id(email: str, task_id: str) -> None:
    """Associa o ID da task Celery ao backup ativo."""
    backup_id = _obter_id_ativo(email)
    if backup_id is None:
        return
    conn = obter_conexao()
    conn.execute(
        "UPDATE backups SET celery_task_id = ? WHERE id = ?",
        (task_id, backup_id),
    )
    conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Leitura
# ─────────────────────────────────────────────────────────────────────────────

def listar_interrompidos_para_recuperacao() -> list:
    """
    Retorna todos os backups que estavam 'em_andamento' antes do restart.
    Deve ser chamada ANTES de marcar_backups_interrompidos() para preservar
    os dados necessários para re-enfileirar os processos.

    Returns:
        Lista de dicts com email, ticket_id, nome e deletar_conta de cada backup interrompido.
    """
    conn = obter_conexao()
    rows = conn.execute(
        "SELECT email, ticket_id, nome, deletar_conta FROM backups WHERE status_geral = 'em_andamento'"
    ).fetchall()
    return [
        {
            "email":        r["email"],
            "ticket_id":    r["ticket_id"],
            "nome":         r["nome"],
            "deletar_conta": bool(r["deletar_conta"]),
        }
        for r in rows
    ]


def existe_backup_em_andamento(email: str) -> bool:
    """True se há um backup ativo para o e-mail."""
    return _obter_id_ativo(email) is not None


def existe_backup_concluido_por_ticket(ticket_id: str) -> bool:
    """True se já existe backup concluído com sucesso para este ticket_id.

    Impede reprocessamento causado por webhooks disparados pelo próprio
    sistema ao atualizar/transicionar o ticket no Jira (ex: transicionar_resolvido
    dispara novo webhook que chega após o backup já ter sido finalizado).
    """
    conn = obter_conexao()
    row = conn.execute(
        "SELECT id FROM backups WHERE ticket_id = ? AND status_geral = 'concluido' LIMIT 1",
        (ticket_id,),
    ).fetchone()
    return row is not None


def listar_ativos() -> list:
    """Retorna todos os backups em andamento com suas etapas."""
    conn = obter_conexao()
    rows = conn.execute(
        """SELECT id, email, ticket_id, nome, status_geral, inicio, fim,
                  link_drive, sha256_zip, erro_mensagem
           FROM backups WHERE status_geral = 'em_andamento'
           ORDER BY inicio DESC"""
    ).fetchall()
    if not rows:
        return []
    etapas = _carregar_etapas_em_lote([r["id"] for r in rows])
    return [_montar_dict(row, etapas.get(row["id"], [])) for row in rows]


def listar_historico(pagina: int = 1, por_pagina: int = 50) -> list:
    """
    Retorna backups finalizados com paginação.

    Args:
        pagina:     Página desejada (começa em 1)
        por_pagina: Quantidade por página (padrão 50)
    """
    conn = obter_conexao()
    offset = (pagina - 1) * por_pagina
    rows = conn.execute(
        """SELECT id, email, ticket_id, nome, status_geral, inicio, fim,
                  link_drive, sha256_zip, erro_mensagem
           FROM backups WHERE status_geral != 'em_andamento'
           ORDER BY inicio DESC LIMIT ? OFFSET ?""",
        (por_pagina, offset),
    ).fetchall()
    if not rows:
        return []
    etapas = _carregar_etapas_em_lote([r["id"] for r in rows])
    return [_montar_dict(row, etapas.get(row["id"], [])) for row in rows]


def obter_por_email(email: str) -> Optional[dict]:
    """Retorna o backup mais recente (ativo ou finalizado) para o e-mail."""
    conn = obter_conexao()
    row = conn.execute(
        """SELECT id, email, ticket_id, nome, status_geral, inicio, fim,
                  link_drive, sha256_zip, erro_mensagem
           FROM backups WHERE email = ?
           ORDER BY inicio DESC LIMIT 1""",
        (email,),
    ).fetchone()
    if not row:
        return None
    etapas = _carregar_etapas_em_lote([row["id"]])
    return _montar_dict(row, etapas.get(row["id"], []))


def listar_backups_stuck(horas: int = 12) -> list:
    """Retorna backups marcados 'em_andamento' há mais de `horas` horas.

    Backup que passa do limite indica:
    - worker travado (D-state, deadlock de FS)
    - export do Vault enorme (>= 24h é comum em Drives grandes)
    - bug que deixou registro órfão

    Usado pelo /health para sinalizar status degradado e disparar
    alerta no monitoramento externo. Não toca no banco — apenas lê.

    Returns:
        Lista de dicts com email, ticket_id, inicio, idade_horas
        (todos os em_andamento com `inicio` > `horas` atrás).
    """
    from datetime import timedelta
    conn = obter_conexao()
    limite = (datetime.now(timezone.utc) - timedelta(hours=horas)).isoformat()
    rows = conn.execute(
        """SELECT email, ticket_id, inicio
           FROM backups
           WHERE status_geral = 'em_andamento' AND inicio < ?
           ORDER BY inicio ASC""",
        (limite,),
    ).fetchall()
    agora = datetime.now(timezone.utc)
    resultado = []
    for r in rows:
        try:
            inicio_dt = datetime.fromisoformat(r["inicio"])
            idade = (agora - inicio_dt).total_seconds() / 3600.0
        except (TypeError, ValueError):
            idade = 0.0
        resultado.append({
            "email":       r["email"],
            "ticket_id":   r["ticket_id"],
            "inicio":      r["inicio"],
            "idade_horas": round(idade, 1),
        })
    return resultado


def contar_erros_por_ticket(ticket_id: str) -> int:
    """Retorna quantos backups com status_geral='erro' existem para este ticket.

    Usado pela recuperação automática para implementar blacklist por
    ticket: se a quantidade for >= MAX_TENTATIVAS_RECUPERACAO, o ticket
    não é re-enfileirado (evita loop infinito quando a causa raiz da
    falha persiste — ex: backup maior que o disco disponível).
    """
    conn = obter_conexao()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM backups WHERE ticket_id = ? AND status_geral = 'erro'",
        (ticket_id,),
    ).fetchone()
    return int(row["n"]) if row else 0


def obter_resumo() -> dict:
    """Retorna contagens por status para o dashboard."""
    conn = obter_conexao()
    rows = conn.execute(
        "SELECT status_geral, COUNT(*) AS total FROM backups GROUP BY status_geral"
    ).fetchall()

    contagem = {r["status_geral"]: r["total"] for r in rows}
    ativos = contagem.get("em_andamento", 0)
    sucessos = contagem.get("concluido", 0)
    erros = contagem.get("erro", 0)

    return {
        "ativos": ativos,
        "total_finalizados": sucessos + erros,
        "sucessos": sucessos,
        "erros": erros,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers privados
# ─────────────────────────────────────────────────────────────────────────────

def _obter_id_ativo(email: str) -> Optional[int]:
    conn = obter_conexao()
    row = conn.execute(
        """SELECT id FROM backups
           WHERE email = ? AND status_geral = 'em_andamento'
           ORDER BY inicio DESC LIMIT 1""",
        (email,),
    ).fetchone()
    return row[0] if row else None


def _carregar_etapas_em_lote(backup_ids: list) -> dict:
    """
    Carrega etapas de múltiplos backups em uma única query.
    Retorna {backup_id: [etapas]} — elimina o problema de N+1 queries.
    """
    if not backup_ids:
        return {}
    placeholders = ",".join("?" * len(backup_ids))
    conn = obter_conexao()
    rows = conn.execute(
        f"""SELECT backup_id, numero, nome, descricao, status, inicio, fim, progresso_pct
            FROM etapas_backup
            WHERE backup_id IN ({placeholders})
            ORDER BY backup_id, numero""",
        backup_ids,
    ).fetchall()
    resultado: dict = {}
    for r in rows:
        bid = r["backup_id"]
        resultado.setdefault(bid, []).append(dict(r))
    return resultado


def _montar_dict(row, etapas: list) -> dict:
    return {
        "email":         row["email"],
        "ticket_id":     row["ticket_id"],
        "nome":          row["nome"],
        "inicio":        row["inicio"],
        "fim":           row["fim"],
        "status_geral":  row["status_geral"],
        "erro_mensagem": row["erro_mensagem"],
        "link_drive":    row["link_drive"],
        "sha256_zip":    row["sha256_zip"],
        "etapas":        etapas,
    }
