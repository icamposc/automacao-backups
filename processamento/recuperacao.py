"""
============================================================
Módulo de Recuperação de Backups — Automação de Backups
============================================================
Versão: 1.1.0
Data:   2026-05-11
Descrição: Recupera automaticamente backups que estavam em
           andamento quando o servidor foi reiniciado.

           Fluxo de recuperação:
           1. Coleta backups interrompidos ANTES de marcá-los como erro
           2. Marca os registros como erro (libera o índice único)
           3. Para cada interrompido, consulta o histórico de falhas
              do ticket: se já houve >= MAX_TENTATIVAS_RECUPERACAO
              falhas, NÃO re-enfileira (blacklist por ticket — evita
              loop infinito quando a causa raiz persiste).
           4. Re-enfileira os demais no Celery.

           O fluxo de backup detecta exports existentes no Vault via
           buscar_exportacao_existente():
           - Se COMPLETED  → retoma a partir do download
           - Se IN_PROGRESS → aguarda a conclusão normalmente
           - Se não existir → recria o export do zero
============================================================
Histórico:
  1.2.0 (2026-06-18) — Só considera "interrompido por restart" um backup
                        cuja tarefa Celery NÃO esteja mais ativa no worker
                        (SPN-64951). Antes, qualquer backup 'em_andamento'
                        era reclamado a CADA inicialização do app — e o app
                        reinicializa a cada respawn de worker do Gunicorn
                        (ex.: WORKER TIMEOUT). Isso gerava falso alerta
                        "Servidor Reiniciou" e tarefas Celery duplicadas
                        para um backup que seguia rodando normalmente.
  1.1.0 (2026-05-11) — Blacklist por ticket após N falhas (P3.3/N7).
                        Evita loop quando o backup é maior que o disco
                        ou outra causa raiz persiste entre tentativas.
  1.0.0 (2026-04-14) — Versão inicial.
============================================================
"""

from utils.logger import obter_logger

logger = obter_logger("recuperacao")

# Limite de tentativas automáticas de recuperação antes de bloquear o ticket.
# Acima deste número, a recuperação NÃO re-enfileira — alerta no Chat e exige
# intervenção humana (a causa raiz provavelmente persiste).
MAX_TENTATIVAS_RECUPERACAO = 3


def _coletar_task_ids_ativos() -> set:
    """
    Consulta o(s) worker(s) Celery e retorna o conjunto de IDs de tarefas
    atualmente em execução (reservadas ou ativas).

    Usado para distinguir um RESTART REAL do servidor (tarefa morreu junto)
    de um mero respawn de worker do Gunicorn (a tarefa segue viva no container
    'worker', que é independente do 'servidor').

    Em caso de falha de comunicação com o worker (timeout, broker indisponível),
    retorna conjunto vazio — interpretado como "nenhuma tarefa viva confirmada",
    levando a recuperação a reclamar o backup (comportamento seguro: prefere
    re-enfileirar a perder um backup órfão).
    """
    try:
        from worker.celery_app import app as celery_app
        inspecao = celery_app.control.inspect(timeout=3)
        ativos = inspecao.active() or {}
        reservados = inspecao.reserved() or {}

        ids = set()
        for grupos in (ativos, reservados):
            for tarefas in grupos.values():
                for tarefa in tarefas:
                    task_id = tarefa.get("id")
                    if task_id:
                        ids.add(task_id)
        return ids
    except Exception as erro:
        logger.warning(
            f"Não foi possível consultar tarefas ativas no Celery "
            f"({erro}). Assumindo nenhuma tarefa viva (recuperação prosseguirá)."
        )
        return set()


def recuperar_backups_interrompidos() -> int:
    """
    Chamado na inicialização do servidor, após inicializar_banco().

    Verifica se havia backups em andamento antes do restart e os
    re-enfileira no Celery para retomada automática, respeitando a
    blacklist por ticket (MAX_TENTATIVAS_RECUPERACAO).

    Returns:
        Número de backups que foram reagendados (exclui os bloqueados).
    """
    from dados.repositorio_backups import (
        listar_interrompidos_para_recuperacao,
        contar_erros_por_ticket,
        finalizar_backup,
    )
    from processamento.orquestrador import iniciar_backup_async

    candidatos = listar_interrompidos_para_recuperacao()

    if not candidatos:
        logger.info("Nenhum backup em andamento — nada a recuperar")
        return 0

    # Distingue restart REAL (tarefa morreu) de respawn de worker do Gunicorn
    # (a tarefa segue viva no container 'worker'). Só é "interrompido" quando a
    # tarefa Celery NÃO está mais ativa.
    ids_ativos = _coletar_task_ids_ativos()

    interrompidos = []
    for backup in candidatos:
        task_id = backup.get("celery_task_id")
        if task_id and task_id in ids_ativos:
            logger.info(
                f"Backup de {backup['email']} (ticket {backup['ticket_id']}) "
                f"segue ATIVO no worker Celery (task {task_id}) — provável respawn "
                f"de worker do Gunicorn, NÃO é restart. Ignorando."
            )
        else:
            interrompidos.append(backup)

    if not interrompidos:
        logger.info(
            f"{len(candidatos)} backup(s) em andamento, todos ainda ativos no "
            f"worker — nenhum restart real detectado, nada a recuperar."
        )
        return 0

    logger.warning(
        f"{len(interrompidos)} backup(s) interrompido(s) pelo restart — "
        f"avaliando blacklist e reagendando..."
    )

    # Marca como erro APENAS os genuinamente interrompidos (libera o índice único
    # e alimenta a blacklist). IMPORTANTE: contar_erros_por_ticket() conta tickets
    # em status='erro', então deve rodar APÓS esta marcação.
    for backup in interrompidos:
        try:
            finalizar_backup(
                backup["email"],
                sucesso=False,
                erro_mensagem="Backup interrompido por reinício do servidor",
            )
        except Exception as erro:
            logger.error(
                f"Falha ao marcar backup interrompido de {backup['email']} como erro: {erro}"
            )

    # Captura lista resumida dos tickets afetados (usada na notificacao consolidada).
    tickets_afetados = [
        {"ticket_id": b.get("ticket_id"), "email": b.get("email")}
        for b in interrompidos
    ]

    reagendados = 0
    bloqueados = 0
    for backup in interrompidos:
        email         = backup["email"]
        ticket_id     = backup["ticket_id"]
        nome          = backup.get("nome")
        deletar_conta = backup.get("deletar_conta", True)

        # Blacklist por ticket: se já houve N falhas seguidas, não re-enfileira.
        n_erros = contar_erros_por_ticket(ticket_id)
        if n_erros >= MAX_TENTATIVAS_RECUPERACAO:
            bloqueados += 1
            logger.error(
                f"BLOQUEADO pela blacklist — ticket {ticket_id} ({email}) "
                f"já falhou {n_erros}x. NÃO re-enfileirado. "
                f"Causa raiz provavelmente persiste — investigar manualmente."
            )
            _notificar_bloqueio(email, ticket_id, nome, n_erros)
            continue

        try:
            logger.info(
                f"Reagendando backup interrompido: {email} "
                f"(ticket: {ticket_id}, falhas anteriores: {n_erros}, "
                f"deletar_conta: {deletar_conta})"
            )
            iniciar_backup_async(email, ticket_id, nome, deletar_conta)
            reagendados += 1
        except Exception as erro:
            logger.error(f"Falha ao reagendar backup de {email}: {erro}")

    logger.info(
        f"Recuperação concluída — {reagendados}/{len(interrompidos)} reagendado(s), "
        f"{bloqueados} bloqueado(s) pela blacklist."
    )

    # Alerta consolidado no chat de LOGS — evento CRITICO (restart com backups ativos).
    # Falhas no envio sao silenciadas para nao afetar o fluxo de recuperacao.
    try:
        from servicos.google_chat import notificar_restart_servidor
        notificar_restart_servidor(
            total_interrompidos=len(interrompidos),
            reagendados=reagendados,
            bloqueados=bloqueados,
            tickets_afetados=tickets_afetados,
        )
    except Exception as erro:
        logger.warning(f"Falha ao enviar alerta de restart ao chat de Logs: {erro}")

    return reagendados


def _notificar_bloqueio(email: str, ticket_id: str, nome: str, n_erros: int) -> None:
    """Envia alerta ao Google Chat avisando que o ticket foi bloqueado.

    Falhas no envio do alerta são silenciadas (logadas) — não devem
    impedir a recuperação dos demais backups da fila.
    """
    try:
        from servicos.google_chat import notificar_erro
        mensagem = (
            f"Recuperacao bloqueada apos {n_erros} falhas consecutivas. "
            f"Causa raiz persiste — investigar manualmente. "
            f"Backup NAO sera re-enfileirado automaticamente."
        )
        notificar_erro(email, ticket_id, mensagem, nome)
    except Exception as erro:
        logger.warning(f"Falha ao enviar alerta de bloqueio para {ticket_id}: {erro}")
