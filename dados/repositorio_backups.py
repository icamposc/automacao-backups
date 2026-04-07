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

from datetime import datetime
from typing import Optional

from dados.banco import obter_conexao
from utils.logger import obter_logger

logger = obter_logger("repositorio_backups")

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

def inserir_backup(email: str, ticket_id: str, nome: str = None) -> int:
    """
    Insere um novo backup e suas 8 etapas. Retorna o ID gerado.
    """
    conn = obter_conexao()
    cursor = conn.execute(
        """INSERT INTO backups (email, ticket_id, nome, status_geral, inicio)
           VALUES (?, ?, ?, 'em_andamento', ?)""",
        (email, ticket_id, nome or email, datetime.now().isoformat()),
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

    agora = datetime.now().isoformat()
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
            datetime.now().isoformat(),
            erro_mensagem,
            link_drive,
            sha256_zip,
            backup_id,
        ),
    )
    conn.commit()
    logger.debug(f"Backup finalizado: id={backup_id}, sucesso={sucesso}")


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
    return [_montar_dict(row) for row in rows]


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
    return [_montar_dict(row) for row in rows]


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
    return _montar_dict(row) if row else None


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


def _carregar_etapas(backup_id: int) -> list:
    conn = obter_conexao()
    rows = conn.execute(
        """SELECT numero, nome, descricao, status, inicio, fim
           FROM etapas_backup WHERE backup_id = ? ORDER BY numero""",
        (backup_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _montar_dict(row) -> dict:
    etapas = _carregar_etapas(row["id"])
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
