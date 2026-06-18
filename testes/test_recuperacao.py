"""
Testes do módulo recuperacao.py

Verifica:
- Blacklist por ticket após N falhas (não re-enfileira)
- Re-enfileiramento normal quando contagem < N
- Caso sem backups interrompidos (no-op)
"""

import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def banco_isolado(banco_teste):
    # Por padrão, simula "nenhuma tarefa Celery viva" — ou seja, um restart real
    # em que os processos morreram. Os testes que exercitam o caso de tarefa viva
    # sobrescrevem este patch internamente.
    with patch("processamento.recuperacao._coletar_task_ids_ativos", return_value=set()):
        yield


def _registrar_falhas(ticket_id: str, email: str, n: int) -> None:
    """Cria n backups com status='erro' para o ticket."""
    from dados.repositorio_backups import inserir_backup, finalizar_backup
    for _ in range(n):
        inserir_backup(email, ticket_id, "Teste")
        finalizar_backup(email, sucesso=False, erro_mensagem="falha de teste")


class TestRecuperacaoBlacklist:
    def test_nao_reenfileira_apos_max_tentativas(self):
        from processamento import recuperacao
        from dados.repositorio_backups import inserir_backup

        ticket = "SPN-9001"
        email = "blacklist@empresa.com"

        # 3 falhas anteriores no histórico
        _registrar_falhas(ticket, email, 3)
        # 1 backup atualmente "em_andamento" (será marcado como erro e a recuperação avalia)
        inserir_backup(email, ticket, "Teste")

        mock_async = MagicMock()
        mock_alerta = MagicMock()
        with patch("processamento.orquestrador.iniciar_backup_async", mock_async), \
             patch("servicos.google_chat.notificar_erro", mock_alerta):
            reagendados = recuperacao.recuperar_backups_interrompidos()

        assert reagendados == 0
        mock_async.assert_not_called()
        mock_alerta.assert_called_once()
        # Confere que o argumento mensagem contém indicação de bloqueio
        chamada = mock_alerta.call_args
        assert "bloqueada" in str(chamada).lower() or "blacklist" in str(chamada).lower() or "Recuperacao bloqueada" in str(chamada)

    def test_reenfileira_quando_abaixo_do_limite(self):
        from processamento import recuperacao
        from dados.repositorio_backups import inserir_backup

        ticket = "SPN-9002"
        email = "tentativa@empresa.com"

        # Apenas 1 falha anterior — abaixo do limite (3)
        _registrar_falhas(ticket, email, 1)
        inserir_backup(email, ticket, "Teste")

        mock_async = MagicMock()
        with patch("processamento.orquestrador.iniciar_backup_async", mock_async):
            reagendados = recuperacao.recuperar_backups_interrompidos()

        assert reagendados == 1
        mock_async.assert_called_once()

    def test_no_op_sem_interrompidos(self):
        from processamento import recuperacao

        mock_async = MagicMock()
        with patch("processamento.orquestrador.iniciar_backup_async", mock_async):
            reagendados = recuperacao.recuperar_backups_interrompidos()

        assert reagendados == 0
        mock_async.assert_not_called()

    def test_nao_reclama_backup_com_tarefa_ainda_ativa(self):
        """SPN-64951: respawn de worker do Gunicorn não pode reclamar um backup
        que segue rodando no worker Celery."""
        from processamento import recuperacao
        from dados.repositorio_backups import (
            inserir_backup, salvar_celery_task_id, existe_backup_em_andamento,
        )

        ticket = "SPN-VIVO"
        email = "vivo@empresa.com"
        inserir_backup(email, ticket, "Teste")
        salvar_celery_task_id(email, "task-viva-123")

        mock_async = MagicMock()
        mock_alerta = MagicMock()
        # A tarefa AINDA está ativa no worker — não é restart real.
        with patch("processamento.recuperacao._coletar_task_ids_ativos",
                   return_value={"task-viva-123"}), \
             patch("processamento.orquestrador.iniciar_backup_async", mock_async), \
             patch("servicos.google_chat.notificar_restart_servidor", mock_alerta):
            reagendados = recuperacao.recuperar_backups_interrompidos()

        assert reagendados == 0
        mock_async.assert_not_called()          # não reagenda
        mock_alerta.assert_not_called()         # não dispara alerta falso de restart
        assert existe_backup_em_andamento(email) is True  # segue em andamento

    def test_reclama_quando_tarefa_morta_mesmo_com_task_id(self):
        """Restart real: a tarefa não está mais ativa → reclama normalmente."""
        from processamento import recuperacao
        from dados.repositorio_backups import inserir_backup, salvar_celery_task_id

        ticket = "SPN-MORTA"
        email = "morta@empresa.com"
        inserir_backup(email, ticket, "Teste")
        salvar_celery_task_id(email, "task-morta-999")

        mock_async = MagicMock()
        # Conjunto de ativos NÃO contém a task → considerada morta.
        with patch("processamento.recuperacao._coletar_task_ids_ativos",
                   return_value={"outra-task-abc"}), \
             patch("processamento.orquestrador.iniciar_backup_async", mock_async), \
             patch("servicos.google_chat.notificar_restart_servidor"):
            reagendados = recuperacao.recuperar_backups_interrompidos()

        assert reagendados == 1
        mock_async.assert_called_once()

    def test_multiplos_tickets_um_bloqueado_outros_nao(self):
        from processamento import recuperacao
        from dados.repositorio_backups import inserir_backup

        ticket_bloqueado = "SPN-A"
        ticket_ok = "SPN-B"
        email_bloqueado = "bloq@empresa.com"
        email_ok = "ok@empresa.com"

        _registrar_falhas(ticket_bloqueado, email_bloqueado, 5)
        _registrar_falhas(ticket_ok, email_ok, 0)

        inserir_backup(email_bloqueado, ticket_bloqueado, "Teste")
        inserir_backup(email_ok, ticket_ok, "Teste")

        mock_async = MagicMock()
        with patch("processamento.orquestrador.iniciar_backup_async", mock_async), \
             patch("servicos.google_chat.notificar_erro"):
            reagendados = recuperacao.recuperar_backups_interrompidos()

        assert reagendados == 1
        # Só o ticket OK foi reagendado
        chamadas_emails = [c.args[0] for c in mock_async.call_args_list]
        assert email_ok in chamadas_emails
        assert email_bloqueado not in chamadas_emails


class TestContarErrosPorTicket:
    def test_zero_quando_sem_falhas(self):
        from dados.repositorio_backups import contar_erros_por_ticket
        assert contar_erros_por_ticket("SPN-VAZIO") == 0

    def test_conta_apenas_status_erro(self):
        from dados.repositorio_backups import (
            inserir_backup, finalizar_backup, contar_erros_por_ticket,
        )
        ticket = "SPN-CONT-1"

        # 2 erros + 1 sucesso para o mesmo ticket (re-tentativas)
        inserir_backup("a@x.com", ticket, "A")
        finalizar_backup("a@x.com", sucesso=False, erro_mensagem="x")
        inserir_backup("b@x.com", ticket, "B")
        finalizar_backup("b@x.com", sucesso=False, erro_mensagem="y")
        inserir_backup("c@x.com", ticket, "C")
        finalizar_backup("c@x.com", sucesso=True, link_drive="link")

        assert contar_erros_por_ticket(ticket) == 2

    def test_nao_conta_em_andamento(self):
        from dados.repositorio_backups import inserir_backup, contar_erros_por_ticket

        ticket = "SPN-CONT-2"
        inserir_backup("ativo@x.com", ticket, "Ativo")
        # Sem finalizar — fica em_andamento
        assert contar_erros_por_ticket(ticket) == 0
