"""
============================================================
Módulo de Banco de Dados SQLite — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-04-02
Descrição: Inicializa e gerencia o banco de dados SQLite
           local. Utiliza WAL mode para suportar múltiplas
           conexões simultâneas (Celery + Flask).
           Cada thread/processo obtém sua própria conexão
           via threading.local().
============================================================
"""

import sqlite3
import threading
from utils.logger import obter_logger

logger = obter_logger("banco")

# Armazenamento de conexões por thread
_local = threading.local()


def obter_conexao() -> sqlite3.Connection:
    """
    Retorna a conexão SQLite da thread atual.
    Cria uma nova conexão se ainda não existir para esta thread.
    """
    if not hasattr(_local, "conn") or _local.conn is None:
        from config.configuracoes import SQLITE_PATH
        SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread omitido (padrão True) — cada thread tem sua própria
        # conexão via threading.local(), portanto nunca há compartilhamento
        conn = sqlite3.connect(str(SQLITE_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA wal_autocheckpoint=1000")
        _local.conn = conn
    return _local.conn


def inicializar_banco() -> None:
    """
    Cria as tabelas do banco se ainda não existirem.
    Deve ser chamada uma vez na inicialização do servidor.
    """
    from config.configuracoes import SQLITE_PATH
    SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(SQLITE_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS backups (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            email           TEXT    NOT NULL,
            ticket_id       TEXT    NOT NULL,
            nome            TEXT,
            status_geral    TEXT    NOT NULL DEFAULT 'em_andamento',
            inicio          TEXT    NOT NULL,
            fim             TEXT,
            link_drive      TEXT,
            sha256_zip      TEXT,
            erro_mensagem   TEXT,
            celery_task_id  TEXT,
            deletar_conta   INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS etapas_backup (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            backup_id     INTEGER NOT NULL,
            numero        INTEGER NOT NULL,
            nome          TEXT    NOT NULL,
            descricao     TEXT,
            status        TEXT    NOT NULL DEFAULT 'pendente',
            inicio        TEXT,
            fim           TEXT,
            progresso_pct INTEGER,
            FOREIGN KEY (backup_id) REFERENCES backups(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_backups_email        ON backups(email);
        CREATE INDEX IF NOT EXISTS idx_backups_status       ON backups(status_geral);
        CREATE INDEX IF NOT EXISTS idx_backups_ticket       ON backups(ticket_id);
        CREATE INDEX IF NOT EXISTS idx_backups_celery       ON backups(celery_task_id);
        CREATE INDEX IF NOT EXISTS idx_backups_email_status  ON backups(email, status_geral);
        CREATE INDEX IF NOT EXISTS idx_backups_ticket_status ON backups(ticket_id, status_geral);
        CREATE INDEX IF NOT EXISTS idx_etapas_backup         ON etapas_backup(backup_id);

        -- Índice único parcial: impede dois backups ativos para o mesmo e-mail.
        -- Cobre 'em_andamento' (worker rodando) e 'aguardando_nas' (aguardando coleta
        -- pelo NAS Synology dentro da janela de 23h). Se um novo webhook chega para o
        -- mesmo email enquanto o anterior ainda nao foi finalizado, o INSERT falha
        -- com IntegrityError e e rejeitado antes de qualquer dano.
        CREATE UNIQUE INDEX IF NOT EXISTS idx_backups_email_ativo
            ON backups(email)
            WHERE status_geral IN ('em_andamento', 'aguardando_nas');
    """)

    conn.commit()

    # Migrações: adiciona colunas em instalações existentes
    migracoes = [
        ("ALTER TABLE etapas_backup ADD COLUMN progresso_pct INTEGER",
         "coluna 'progresso_pct' adicionada à tabela etapas_backup"),
        ("ALTER TABLE backups ADD COLUMN deletar_conta INTEGER NOT NULL DEFAULT 1",
         "coluna 'deletar_conta' adicionada à tabela backups"),
        ("ALTER TABLE backups ADD COLUMN inicio_aguardando_nas TEXT",
         "coluna 'inicio_aguardando_nas' adicionada à tabela backups"),
    ]
    for sql, descricao in migracoes:
        try:
            conn.execute(sql)
            conn.commit()
            logger.info(f"Migração aplicada: {descricao}")
        except sqlite3.OperationalError:
            pass  # Coluna já existe

    # Recria o índice único parcial para garantir que bancos antigos (que tinham o
    # filtro apenas com 'em_andamento') passem a cobrir também 'aguardando_nas'.
    # SQLite nao tem CREATE OR REPLACE INDEX, entao DROP+CREATE.
    try:
        conn.execute("DROP INDEX IF EXISTS idx_backups_email_ativo")
        conn.execute(
            "CREATE UNIQUE INDEX idx_backups_email_ativo "
            "ON backups(email) "
            "WHERE status_geral IN ('em_andamento', 'aguardando_nas')"
        )
        conn.commit()
    except sqlite3.OperationalError as erro:
        logger.warning(f"Falha ao recriar idx_backups_email_ativo: {erro}")

    conn.close()
    logger.info(f"Banco de dados inicializado: {SQLITE_PATH}")


def fechar_conexao_thread() -> None:
    """
    Fecha e remove a conexão SQLite da thread atual.
    Usado principalmente em testes para garantir isolamento entre casos.
    """
    if hasattr(_local, "conn") and _local.conn is not None:
        try:
            _local.conn.close()
        except Exception:
            pass
        _local.conn = None


def marcar_backups_interrompidos() -> int:
    """
    Marca como erro todos os backups que estavam 'em_andamento'
    antes do restart. Eles não têm mais um worker executando.

    Returns:
        Número de backups marcados como erro.
    """
    from config.configuracoes import SQLITE_PATH
    conn = sqlite3.connect(str(SQLITE_PATH))
    cursor = conn.execute(
        """UPDATE backups
           SET status_geral  = 'erro',
               fim           = datetime('now'),
               erro_mensagem = 'Backup interrompido por reinício do servidor'
           WHERE status_geral = 'em_andamento'"""
    )
    afetados = cursor.rowcount
    conn.commit()
    conn.close()
    if afetados:
        logger.warning(
            f"{afetados} backup(s) interrompido(s) pelo restart foram marcados como erro"
        )
    return afetados
