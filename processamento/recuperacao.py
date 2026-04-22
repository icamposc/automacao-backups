"""
============================================================
Módulo de Recuperação de Backups — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-04-14
Descrição: Recupera automaticamente backups que estavam em
           andamento quando o servidor foi reiniciado.

           Fluxo de recuperação:
           1. Coleta backups interrompidos ANTES de marcá-los como erro
           2. Marca os registros como erro (libera o índice único)
           3. Re-enfileira cada backup no Celery

           O próprio fluxo de backup detecta exports existentes
           no Vault via buscar_exportacao_existente():
           - Se COMPLETED  → retoma a partir do download
           - Se IN_PROGRESS → aguarda a conclusão normalmente
           - Se não existir → recria o export do zero

           Isso garante que o trabalho mais longo (criação e
           monitoramento de exports, que pode durar horas) não
           seja perdido após um restart.
============================================================
"""

from utils.logger import obter_logger

logger = obter_logger("recuperacao")


def recuperar_backups_interrompidos() -> int:
    """
    Chamado na inicialização do servidor, após inicializar_banco().

    Verifica se havia backups em andamento antes do restart e os
    re-enfileira no Celery para retomada automática.

    Returns:
        Número de backups que foram reagendados.
    """
    from dados.repositorio_backups import listar_interrompidos_para_recuperacao
    from dados.banco import marcar_backups_interrompidos
    from processamento.orquestrador import iniciar_backup_async

    # Coleta os dados ANTES de marcar como erro (após a marcação o registro
    # não tem mais status 'em_andamento' e não seria encontrado)
    interrompidos = listar_interrompidos_para_recuperacao()

    if not interrompidos:
        logger.info("Nenhum backup interrompido encontrado — nada a recuperar")
        marcar_backups_interrompidos()
        return 0

    logger.warning(
        f"{len(interrompidos)} backup(s) interrompido(s) pelo restart — "
        f"marcando como erro e reagendando..."
    )

    # Marca como erro para liberar o índice único (permite novo INSERT para o mesmo e-mail)
    marcar_backups_interrompidos()

    reagendados = 0
    for backup in interrompidos:
        email        = backup["email"]
        ticket_id    = backup["ticket_id"]
        nome         = backup.get("nome")
        deletar_conta = backup.get("deletar_conta", True)
        try:
            logger.info(
                f"Reagendando backup interrompido: {email} "
                f"(ticket: {ticket_id}, deletar_conta: {deletar_conta})"
            )
            iniciar_backup_async(email, ticket_id, nome, deletar_conta)
            reagendados += 1
        except Exception as erro:
            logger.error(f"Falha ao reagendar backup de {email}: {erro}")

    logger.info(f"Recuperação concluída — {reagendados}/{len(interrompidos)} backup(s) reagendado(s)")
    return reagendados
