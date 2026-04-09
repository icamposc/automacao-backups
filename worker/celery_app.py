"""
============================================================
Configuração do Celery — Automação de Backups
============================================================
Versão: 1.1.0
Data: 2026-04-08
Descrição: Instância e configuração do Celery com Redis
           como broker e backend. Usa pool=threads com
           concurrency=4 para backups simultâneos com
           semáforo compartilhado no mesmo processo.
============================================================
Histórico:
  1.1.0 (2026-04-08) — Fecha conexão SQLite após cada task
                        via sinal task_postrun (correção de
                        acúmulo em threading.local)
  1.0.0 (2026-04-02) — Versão inicial
============================================================
"""

from celery import Celery
from celery.signals import task_postrun

# A importação de configuracoes é feita aqui para que o
# worker também carregue o .env ao iniciar
from config.configuracoes import REDIS_URL

app = Celery(
    "automacao_backups",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["worker.tarefas"],
)

app.conf.update(
    # Serialização
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",

    # Resultados expiram em 24h
    result_expires=86400,

    # Confirma a tarefa APÓS execução — garante que não se perca em caso de crash
    task_acks_late=True,

    # Cada thread recebe apenas 1 tarefa por vez — evita acúmulo indevido
    worker_prefetch_multiplier=1,

    # Timeout de tarefa não é configurado globalmente aqui pois --pool=threads
    # não suporta time_limit baseado em sinal (SIGALRM). O timeout de exportação
    # é controlado em nível de aplicação via TIMEOUT_MAXIMO_SEGUNDOS no Vault.

    # Timezone
    timezone="America/Sao_Paulo",
    enable_utc=True,
)


@task_postrun.connect
def fechar_conexao_banco(**kwargs) -> None:
    """
    Fecha a conexão SQLite da thread atual após cada task Celery.

    Com --pool=threads, as threads são reutilizadas entre tasks. Sem este
    handler, a conexão permaneceria aberta indefinidamente na thread,
    acumulando transações e impedindo o checkpoint do WAL mode.

    O sinal task_postrun é disparado mesmo quando a task falha, garantindo
    o fechamento em todos os cenários.
    """
    from dados.banco import fechar_conexao_thread
    fechar_conexao_thread()
