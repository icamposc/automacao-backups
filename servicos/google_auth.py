"""
============================================================
Módulo de Autenticação Google — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-02-19
Descrição: Gerencia autenticação com o Google usando Service
           Account com Domain-Wide Delegation. Fornece
           funções para obter clientes autenticados dos
           serviços Google Vault, Drive e Cloud Storage.
============================================================
Histórico:
  1.0.0 (2026-02-19) — Versão inicial
============================================================
"""

from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.cloud import storage

from config.configuracoes import GOOGLE_CREDENCIAIS_PATH, GOOGLE_ADMIN_EMAIL
from utils.logger import obter_logger

logger = obter_logger("google_auth")

# Escopos necessários para cada serviço do Google
# - ediscovery: Gerenciar exportações no Google Vault
# - devstorage.read_only: Baixar arquivos exportados do Cloud Storage
# - drive: Upload de arquivos para o Google Drive Compartilhado
SCOPES = [
    "https://www.googleapis.com/auth/ediscovery",
    "https://www.googleapis.com/auth/devstorage.read_only",
    "https://www.googleapis.com/auth/drive",
]


def _obter_credenciais() -> service_account.Credentials:
    """
    Carrega as credenciais da Service Account a partir do arquivo JSON
    e configura a delegação de domínio (Domain-Wide Delegation).

    A delegação de domínio permite que a Service Account atue em nome
    do administrador do Google Workspace, o que é necessário para
    acessar o Vault e os dados dos usuários.

    Returns:
        Credenciais autenticadas com delegação de domínio
    """
    logger.info(f"Carregando credenciais da Service Account: {GOOGLE_CREDENCIAIS_PATH}")

    # Carrega o arquivo JSON da Service Account
    credenciais = service_account.Credentials.from_service_account_file(
        GOOGLE_CREDENCIAIS_PATH,
        scopes=SCOPES,
    )

    # Configura delegação de domínio — atua em nome do admin
    credenciais_delegadas = credenciais.with_subject(GOOGLE_ADMIN_EMAIL)

    logger.info(f"Credenciais carregadas com delegação para: {GOOGLE_ADMIN_EMAIL}")
    return credenciais_delegadas


def obter_servico_vault():
    """
    Cria e retorna um cliente autenticado da API do Google Vault.

    O Vault é usado para criar exportações de e-mail e Drive dos
    colaboradores desligados.

    Returns:
        Objeto de serviço da API do Vault (v1)
    """
    logger.info("Criando cliente da API do Google Vault...")
    credenciais = _obter_credenciais()
    servico = build("vault", "v1", credentials=credenciais)
    logger.info("Cliente do Google Vault criado com sucesso")
    return servico


def obter_servico_drive():
    """
    Cria e retorna um cliente autenticado da API do Google Drive.

    O Drive é usado para fazer upload dos arquivos .zip para o
    Shared Drive "MM - Tech - ITO - Backups".

    Returns:
        Objeto de serviço da API do Drive (v3)
    """
    logger.info("Criando cliente da API do Google Drive...")
    credenciais = _obter_credenciais()
    servico = build("drive", "v3", credentials=credenciais)
    logger.info("Cliente do Google Drive criado com sucesso")
    return servico


def obter_cliente_storage() -> storage.Client:
    """
    Cria e retorna um cliente autenticado do Google Cloud Storage.

    O Cloud Storage é usado para baixar os arquivos que o Google Vault
    gera quando uma exportação é concluída. Os arquivos ficam em
    buckets temporários do Google.

    Returns:
        Cliente autenticado do Cloud Storage
    """
    logger.info("Criando cliente do Google Cloud Storage...")
    credenciais = _obter_credenciais()
    cliente = storage.Client(credentials=credenciais)
    logger.info("Cliente do Cloud Storage criado com sucesso")
    return cliente
