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

import os
import httplib2
import google_auth_httplib2
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.cloud import storage

from config.configuracoes import GOOGLE_CREDENCIAIS_PATH, GOOGLE_ADMIN_EMAIL, GOOGLE_DOMINIOS_ADMIN
from utils.logger import obter_logger

logger = obter_logger("google_auth")

# Escopos necessários para cada serviço do Google
# - ediscovery: Gerenciar exportações no Google Vault
# - devstorage.read_only: Baixar arquivos exportados do Cloud Storage
# - drive: Upload de arquivos para o Google Drive Compartilhado
# - admin.directory.user: Gerenciar contas de usuário no Google Workspace
SCOPES = [
    "https://www.googleapis.com/auth/ediscovery",
    "https://www.googleapis.com/auth/devstorage.read_only",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/admin.directory.user",
]


def _obter_http_autorizado(credenciais: service_account.Credentials) -> google_auth_httplib2.AuthorizedHttp:
    """
    Cria um cliente HTTP autorizado para uso com as APIs do Google.

    Em ambientes com proxy corporativo (ex: Netskope), o bundle de certificados
    padrão pode não ser suficiente. Usamos REQUESTS_CA_BUNDLE ou SSL_CERT_FILE
    se definidos, para garantir que o certificado corporativo seja aceito.
    """
    ca_bundle = os.getenv("REQUESTS_CA_BUNDLE") or os.getenv("SSL_CERT_FILE")
    http = httplib2.Http(ca_certs=ca_bundle) if ca_bundle else httplib2.Http()
    return google_auth_httplib2.AuthorizedHttp(credenciais, http=http)


def _resolver_admin_email(email_usuario: str = None) -> str:
    """
    Resolve o e-mail do admin para o domínio do usuário.

    Se GOOGLE_DOMINIOS_ADMIN tiver uma entrada para o domínio do usuário,
    usa esse admin. Caso contrário, usa GOOGLE_ADMIN_EMAIL como fallback.

    Args:
        email_usuario: E-mail do colaborador (ex: joao@filial.com.br)

    Returns:
        E-mail do administrador do domínio correspondente
    """
    if email_usuario and GOOGLE_DOMINIOS_ADMIN:
        dominio = email_usuario.split("@")[-1].lower()
        admin = GOOGLE_DOMINIOS_ADMIN.get(dominio)
        if admin:
            logger.debug(f"Admin resolvido para domínio '{dominio}': {admin}")
            return admin
    return GOOGLE_ADMIN_EMAIL


def _obter_credenciais(email_usuario: str = None) -> service_account.Credentials:
    """
    Carrega as credenciais da Service Account e configura a delegação
    de domínio (Domain-Wide Delegation).

    Args:
        email_usuario: E-mail do colaborador — usado para resolver qual
                       admin do domínio deve ser impersonado.

    Returns:
        Credenciais autenticadas com delegação de domínio
    """
    admin_email = _resolver_admin_email(email_usuario)
    logger.info(f"Carregando credenciais da Service Account: {GOOGLE_CREDENCIAIS_PATH}")

    credenciais = service_account.Credentials.from_service_account_file(
        GOOGLE_CREDENCIAIS_PATH,
        scopes=SCOPES,
    )
    credenciais_delegadas = credenciais.with_subject(admin_email)

    logger.info(f"Credenciais carregadas com delegação para: {admin_email}")
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
    http = _obter_http_autorizado(credenciais)
    servico = build("vault", "v1", http=http)
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
    http = _obter_http_autorizado(credenciais)
    servico = build("drive", "v3", http=http)
    logger.info("Cliente do Google Drive criado com sucesso")
    return servico


def obter_servico_admin(email_usuario: str = None):
    """
    Cria e retorna um cliente autenticado da API do Google Admin (Directory).

    O Admin SDK é usado para gerenciar contas de usuário no Google Workspace,
    incluindo a exclusão de contas de colaboradores desligados.

    Args:
        email_usuario: E-mail do colaborador — usado para resolver o admin
                       correto em ambientes com múltiplos domínios.

    Returns:
        Objeto de serviço da API do Admin Directory (v1)
    """
    logger.info("Criando cliente da API do Google Admin Directory...")
    credenciais = _obter_credenciais(email_usuario)
    http = _obter_http_autorizado(credenciais)
    servico = build("admin", "directory_v1", http=http)
    logger.info("Cliente do Google Admin Directory criado com sucesso")
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
