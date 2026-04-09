"""
============================================================
Módulo Rastreador de Status — Automação de Backups
============================================================
Versão: 2.0.0
Data: 2026-04-02
Descrição: Rastreia o progresso de cada backup por colaborador.
           Persiste o estado no banco de dados SQLite para
           sobreviver a reinicializações do servidor.
           Mantém a mesma interface pública da versão anterior.
============================================================
Histórico:
  2.0.0 (2026-04-02) — Persistência SQLite (melhoria #1 e #6)
  1.0.0 (2026-03-10) — Versão inicial (memória)
============================================================
"""

from typing import Optional

from utils.logger import obter_logger

logger = obter_logger("rastreador")

# Definição das etapas do fluxo de backup
ETAPAS = [
    {"numero": 1, "nome": "Notificação Jira",     "descricao": "Notificando Jira sobre início do backup"},
    {"numero": 2, "nome": "Criar Exportações",    "descricao": "Criando exportações de E-mail e Drive no Vault"},
    {"numero": 3, "nome": "Monitorar Exportações","descricao": "Aguardando conclusão das exportações"},
    {"numero": 4, "nome": "Baixar Arquivos",      "descricao": "Baixando arquivos exportados do Cloud Storage"},
    {"numero": 5, "nome": "Compactar ZIP",        "descricao": "Compactando arquivos em ZIP"},
    {"numero": 6, "nome": "Upload Drive",         "descricao": "Enviando backup para Google Drive Compartilhado"},
    {"numero": 7, "nome": "Atualizar Jira",       "descricao": "Atualizando ticket Jira com resultado"},
    {"numero": 8, "nome": "Excluir Conta",        "descricao": "Verificando backup e excluindo conta"},
]

# Status possíveis para cada etapa
STATUS_PENDENTE    = "pendente"
STATUS_EM_ANDAMENTO = "em_andamento"
STATUS_CONCLUIDO   = "concluido"
STATUS_ERRO        = "erro"


def registrar_backup(email: str, ticket_id: str, nome: str = None) -> None:
    """
    Registra um novo backup no banco de dados.

    Raises:
        ValueError: se já houver backup ativo para este e-mail (race condition).
                    O orquestrador deve capturar e encerrar a task silenciosamente.
    """
    from dados.repositorio_backups import inserir_backup
    inserir_backup(email, ticket_id, nome)
    logger.info(f"Backup registrado: {email} (Ticket: {ticket_id})")


def atualizar_etapa(email: str, numero_etapa: int, status: str) -> None:
    """Atualiza o status de uma etapa específica do backup."""
    from dados.repositorio_backups import atualizar_etapa as _atualizar
    _atualizar(email, numero_etapa, status)
    logger.debug(f"Etapa {numero_etapa} de {email} → {status}")


def finalizar_backup(
    email: str,
    sucesso: bool,
    erro_mensagem: str = None,
    link_drive: str = None,
    sha256_zip: str = None,
) -> None:
    """Marca o backup como finalizado (sucesso ou erro)."""
    from dados.repositorio_backups import finalizar_backup as _finalizar
    _finalizar(email, sucesso, erro_mensagem, link_drive, sha256_zip)
    logger.info(f"Backup finalizado: {email} (sucesso={sucesso})")


def obter_backups_ativos() -> list:
    """Retorna lista de backups em andamento."""
    from dados.repositorio_backups import listar_ativos
    return listar_ativos()


def obter_historico(pagina: int = 1, por_pagina: int = 50) -> list:
    """
    Retorna histórico de backups finalizados com paginação.

    Args:
        pagina:     Página desejada (começa em 1)
        por_pagina: Registros por página (padrão 50)
    """
    from dados.repositorio_backups import listar_historico
    return listar_historico(pagina=pagina, por_pagina=por_pagina)


def obter_backup(email: str) -> Optional[dict]:
    """Retorna o backup mais recente para o e-mail informado."""
    from dados.repositorio_backups import obter_por_email
    return obter_por_email(email)


def obter_resumo() -> dict:
    """Retorna resumo geral para o dashboard."""
    from dados.repositorio_backups import obter_resumo as _resumo
    return _resumo()
