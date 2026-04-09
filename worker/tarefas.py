"""
============================================================
Tarefas Celery — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-04-02
Descrição: Define as tarefas assíncronas executadas pelo
           worker Celery. A tarefa principal envolve o
           orquestrador de backup.
============================================================
"""

from worker.celery_app import app
from processamento.orquestrador import executar_backup_direto, registrar_celery_task_id
from utils.logger import obter_logger

logger = obter_logger("tarefas")

@app.task(
    name="tarefas.executar_backup",
    bind=True,
    max_retries=0,  # Sem retry automático — orquestrador já tem retry interno por etapa
    # time_limit não é usado: --pool=threads não suporta SIGALRM. O timeout de
    # exportação (TIMEOUT_MAXIMO_SEGUNDOS) é controlado no nível da aplicação.
)
def executar_backup(self, email: str, ticket_id: str, nome: str = None) -> None:
    """
    Tarefa Celery que executa o fluxo completo de backup.

    Chamada via: executar_backup.delay(email, ticket_id, nome)

    Args:
        email:     E-mail do colaborador desligado
        ticket_id: Chave do ticket no Jira
        nome:      Nome do colaborador (opcional)
    """
    logger.info(
        f"Tarefa Celery iniciada — "
        f"Task ID: {self.request.id}, E-mail: {email}, Ticket: {ticket_id}"
    )

    # Associa o ID da task Celery ao backup no banco
    registrar_celery_task_id(email, self.request.id)

    # Executa o backup de forma síncrona dentro do worker
    executar_backup_direto(email, ticket_id, nome)

    logger.info(f"Tarefa Celery concluída — Task ID: {self.request.id}, E-mail: {email}")
