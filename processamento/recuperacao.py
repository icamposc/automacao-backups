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
    )
    from dados.banco import marcar_backups_interrompidos
    from processamento.orquestrador import iniciar_backup_async

    interrompidos = listar_interrompidos_para_recuperacao()

    if not interrompidos:
        logger.info("Nenhum backup interrompido encontrado — nada a recuperar")
        marcar_backups_interrompidos()
        return 0

    logger.warning(
        f"{len(interrompidos)} backup(s) interrompido(s) pelo restart — "
        f"avaliando blacklist e reagendando..."
    )

    # Marca como erro para liberar o índice único (permite novo INSERT para o mesmo e-mail).
    # IMPORTANTE: contar_erros_por_ticket() abaixo conta tickets em status='erro',
    # então deve rodar APÓS esta marcação para incluir a falha atual no total.
    marcar_backups_interrompidos()

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
