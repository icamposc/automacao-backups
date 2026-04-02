"""
============================================================
Configuração do Celery — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-04-02
Descrição: Instância e configuração do Celery com Redis
           como broker e backend. Usa um único worker
           (concurrency=1) para respeitar o limite de
           exports simultâneos do Google Vault.
============================================================
"""

from celery import Celery

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

    # Um backup por vez por worker — respeita o semáforo de exports do Vault
    worker_prefetch_multiplier=1,

    # Limite total de 5 horas por tarefa (exports grandes podem demorar)
    task_time_limit=18000,

    # Alerta soft 10 minutos antes do hard limit
    task_soft_time_limit=17400,

    # Timezone
    timezone="America/Sao_Paulo",
    enable_utc=True,
)
