"""
============================================================
Módulo Rastreador de Status — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-03-10
Descrição: Rastreia o progresso de cada backup por colaborador.
           Armazena o status de cada etapa em memória e fornece
           dados para o Dashboard e notificações do Google Chat.
============================================================
Histórico:
  1.0.0 (2026-03-10) — Versão inicial
============================================================
"""

import threading
from datetime import datetime
from typing import Optional

from utils.logger import obter_logger

logger = obter_logger("rastreador")

# Definição das etapas do fluxo de backup
ETAPAS = [
    {"numero": 1, "nome": "Notificação Jira", "descricao": "Notificando Jira sobre início do backup"},
    {"numero": 2, "nome": "Criar Exportações", "descricao": "Criando exportações de E-mail e Drive no Vault"},
    {"numero": 3, "nome": "Monitorar Exportações", "descricao": "Aguardando conclusão das exportações"},
    {"numero": 4, "nome": "Baixar Arquivos", "descricao": "Baixando arquivos exportados do Cloud Storage"},
    {"numero": 5, "nome": "Compactar ZIP", "descricao": "Compactando arquivos em ZIP"},
    {"numero": 6, "nome": "Upload Drive", "descricao": "Enviando backup para Google Drive Compartilhado"},
    {"numero": 7, "nome": "Atualizar Jira", "descricao": "Atualizando ticket Jira com resultado"},
    {"numero": 8, "nome": "Excluir Conta", "descricao": "Verificando backup e excluindo conta"},
]

# Status possíveis para cada etapa
STATUS_PENDENTE = "pendente"
STATUS_EM_ANDAMENTO = "em_andamento"
STATUS_CONCLUIDO = "concluido"
STATUS_ERRO = "erro"

# Armazenamento em memória — Thread-safe
# Estrutura: { email: { "ticket_id": str, "nome": str, "etapas": [...], "inicio": datetime, ... } }
_backups = {}
_lock = threading.Lock()

# Histórico de backups finalizados (mantém os últimos 50)
_historico = []
_MAX_HISTORICO = 50


def registrar_backup(email: str, ticket_id: str, nome: str = None) -> None:
    """Registra um novo backup no rastreador."""
    with _lock:
        _backups[email] = {
            "email": email,
            "ticket_id": ticket_id,
            "nome": nome or email,
            "inicio": datetime.now().isoformat(),
            "fim": None,
            "status_geral": STATUS_EM_ANDAMENTO,
            "erro_mensagem": None,
            "link_drive": None,
            "etapas": [
                {
                    "numero": etapa["numero"],
                    "nome": etapa["nome"],
                    "descricao": etapa["descricao"],
                    "status": STATUS_PENDENTE,
                    "inicio": None,
                    "fim": None,
                }
                for etapa in ETAPAS
            ],
        }
    logger.info(f"Backup registrado no rastreador: {email} (Ticket: {ticket_id})")


def atualizar_etapa(email: str, numero_etapa: int, status: str) -> None:
    """Atualiza o status de uma etapa específica do backup."""
    with _lock:
        backup = _backups.get(email)
        if not backup:
            logger.warning(f"Backup não encontrado no rastreador: {email}")
            return

        for etapa in backup["etapas"]:
            if etapa["numero"] == numero_etapa:
                etapa["status"] = status
                agora = datetime.now().isoformat()

                if status == STATUS_EM_ANDAMENTO:
                    etapa["inicio"] = agora
                elif status in (STATUS_CONCLUIDO, STATUS_ERRO):
                    etapa["fim"] = agora

                break

    logger.debug(f"Etapa {numero_etapa} de {email} → {status}")


def finalizar_backup(email: str, sucesso: bool, erro_mensagem: str = None,
                     link_drive: str = None) -> None:
    """Marca o backup como finalizado (sucesso ou erro)."""
    with _lock:
        backup = _backups.get(email)
        if not backup:
            return

        backup["fim"] = datetime.now().isoformat()
        backup["status_geral"] = STATUS_CONCLUIDO if sucesso else STATUS_ERRO
        backup["erro_mensagem"] = erro_mensagem
        backup["link_drive"] = link_drive

        # Move para o histórico
        copia = dict(backup)
        _historico.insert(0, copia)

        # Mantém apenas os últimos N registros no histórico
        while len(_historico) > _MAX_HISTORICO:
            _historico.pop()

        # Remove do mapa de ativos
        del _backups[email]

    logger.info(f"Backup finalizado no rastreador: {email} (sucesso={sucesso})")


def obter_backups_ativos() -> list:
    """Retorna lista de backups em andamento."""
    with _lock:
        return list(_backups.values())


def obter_historico() -> list:
    """Retorna histórico de backups finalizados."""
    with _lock:
        return list(_historico)


def obter_backup(email: str) -> Optional[dict]:
    """Retorna dados de um backup específico (ativo ou no histórico)."""
    with _lock:
        if email in _backups:
            return dict(_backups[email])

        for item in _historico:
            if item["email"] == email:
                return dict(item)

    return None


def obter_resumo() -> dict:
    """Retorna resumo geral para o dashboard."""
    with _lock:
        ativos = len(_backups)
        total_historico = len(_historico)
        sucessos = sum(1 for b in _historico if b["status_geral"] == STATUS_CONCLUIDO)
        erros = sum(1 for b in _historico if b["status_geral"] == STATUS_ERRO)

    return {
        "ativos": ativos,
        "total_finalizados": total_historico,
        "sucessos": sucessos,
        "erros": erros,
    }
