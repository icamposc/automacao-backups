"""
============================================================
Módulo de Configuração — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-02-19
Descrição: Carrega e valida todas as variáveis de ambiente
           necessárias para o funcionamento do sistema.
           Utiliza o arquivo .env na raiz do projeto.
============================================================
Histórico:
  1.0.0 (2026-02-19) — Versão inicial
============================================================
"""

import json
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Carrega o arquivo .env que fica na raiz do projeto (um nível acima de /config)
_RAIZ_PROJETO = Path(__file__).resolve().parent.parent
load_dotenv(_RAIZ_PROJETO / ".env")


# ============================================================
# Certificado SSL — Ambientes corporativos (ex: Netskope)
# ============================================================
# Em redes com proxy/firewall que interceptam HTTPS, o bundle
# padrão do Python (certifi) não reconhece o certificado corporativo.
# Configuramos o REQUESTS_CA_BUNDLE para usar os certificados do sistema.
_CA_BUNDLE_SISTEMA = "/etc/ssl/certs/ca-certificates.crt"
if not os.getenv("REQUESTS_CA_BUNDLE") and Path(_CA_BUNDLE_SISTEMA).exists():
    os.environ["REQUESTS_CA_BUNDLE"] = _CA_BUNDLE_SISTEMA
if not os.getenv("SSL_CERT_FILE") and Path(_CA_BUNDLE_SISTEMA).exists():
    os.environ["SSL_CERT_FILE"] = _CA_BUNDLE_SISTEMA


def _obter_variavel(nome: str, obrigatoria: bool = True, padrao: str = None) -> str:
    """
    Busca uma variável de ambiente pelo nome.
    Se for obrigatória e não existir, encerra o programa com erro claro.
    """
    valor = os.getenv(nome, padrao)
    if obrigatoria and not valor:
        print(f"[ERRO FATAL] Variável de ambiente '{nome}' não está definida.")
        print(f"  → Verifique o arquivo .env na raiz do projeto: {_RAIZ_PROJETO / '.env'}")
        sys.exit(1)
    return valor


# ============================================================
# Caminhos do projeto
# ============================================================
RAIZ_PROJETO = _RAIZ_PROJETO

# Permite sobrescrever via env para apontar para volumes externos (ex: HDD no Docker)
_pasta_logs_env = os.getenv("PASTA_LOGS", "")
_pasta_temp_env = os.getenv("PASTA_TEMP", "")
_pasta_vault_env = os.getenv("PASTA_VAULT", "")

PASTA_LOGS = Path(_pasta_logs_env) if _pasta_logs_env else Path("/mnt/hdd/logs")
PASTA_TEMP = Path(_pasta_temp_env) if _pasta_temp_env else _RAIZ_PROJETO / "temp"
PASTA_VAULT = Path(_pasta_vault_env) if _pasta_vault_env else Path("/mnt/hdd/vault")

# Cria as pastas se não existirem
PASTA_LOGS.mkdir(parents=True, exist_ok=True)
PASTA_TEMP.mkdir(parents=True, exist_ok=True)
PASTA_VAULT.mkdir(parents=True, exist_ok=True)

# ============================================================
# Google — Service Account e delegação de domínio
# ============================================================
GOOGLE_CREDENCIAIS_PATH = _obter_variavel("GOOGLE_CREDENCIAIS_PATH")
GOOGLE_ADMIN_EMAIL = _obter_variavel("GOOGLE_ADMIN_EMAIL")

# Mapeamento de domínio → e-mail do admin para múltiplos domínios.
# Formato JSON: '{"empresa.com.br":"admin@empresa.com.br","filial.com":"admin@filial.com"}'
# Se um domínio não estiver aqui, usa GOOGLE_ADMIN_EMAIL como fallback.
_dominios_raw = _obter_variavel("GOOGLE_DOMINIOS_ADMIN", obrigatoria=False, padrao="{}")
try:
    GOOGLE_DOMINIOS_ADMIN: dict = json.loads(_dominios_raw)
except json.JSONDecodeError:
    print(f"[AVISO] GOOGLE_DOMINIOS_ADMIN contém JSON inválido. Usando fallback: GOOGLE_ADMIN_EMAIL")
    GOOGLE_DOMINIOS_ADMIN = {}

# ============================================================
# Google Vault — Matter para exportações
# ============================================================
VAULT_MATTER_ID = _obter_variavel("VAULT_MATTER_ID")

# ============================================================
# Google Drive — Pasta de destino no Shared Drive
# ============================================================
DRIVE_PASTA_DESTINO_ID = _obter_variavel("DRIVE_PASTA_DESTINO_ID")

# ============================================================
# Jira Service Management — Integração com tickets
# ============================================================
JIRA_URL_BASE = _obter_variavel("JIRA_URL_BASE")
JIRA_EMAIL = _obter_variavel("JIRA_EMAIL")
JIRA_API_TOKEN = _obter_variavel("JIRA_API_TOKEN")
JIRA_WEBHOOK_SEGREDO = _obter_variavel("JIRA_WEBHOOK_SEGREDO", obrigatoria=False, padrao="")
JIRA_TRANSICAO_EM_ANALISE = _obter_variavel("JIRA_TRANSICAO_EM_ANALISE", obrigatoria=False, padrao="")
JIRA_TRANSICAO_RESOLVIDO = _obter_variavel("JIRA_TRANSICAO_RESOLVIDO", obrigatoria=False, padrao="")
# Cloud ID da instância Atlassian (necessário para a API de Formulários)
# Obtido em: https://<sua-instancia>.atlassian.net/_edge/tenant_info
JIRA_CLOUD_ID = _obter_variavel("JIRA_CLOUD_ID", obrigatoria=False, padrao="")

# ============================================================
# Servidor Flask
# ============================================================
SERVIDOR_PORTA = int(_obter_variavel("SERVIDOR_PORTA", obrigatoria=False, padrao="5000"))
SERVIDOR_HOST = _obter_variavel("SERVIDOR_HOST", obrigatoria=False, padrao="0.0.0.0")

# ============================================================
# Google Chat — Webhook para notificações
# ============================================================
GOOGLE_CHAT_WEBHOOK_URL = _obter_variavel("GOOGLE_CHAT_WEBHOOK_URL", obrigatoria=False, padrao="")

# ============================================================
# Limites de processamento
# ============================================================
# Intervalo entre verificações de status do export (segundos)
POLLING_INTERVALO_SEGUNDOS = int(
    _obter_variavel("POLLING_INTERVALO_SEGUNDOS", obrigatoria=False, padrao="60")
)

# Tempo máximo de espera para um export completar (segundos) — padrão 24 horas
# Exports de Drive com muitos arquivos (15k+) podem levar mais de 6h
TIMEOUT_MAXIMO_SEGUNDOS = int(
    _obter_variavel("TIMEOUT_MAXIMO_SEGUNDOS", obrigatoria=False, padrao="86400")
)

# Número máximo de exports simultâneos (limite do Google é 20, usamos 18 por segurança)
MAX_EXPORTS_SIMULTANEOS = int(
    _obter_variavel("MAX_EXPORTS_SIMULTANEOS", obrigatoria=False, padrao="18")
)

# ============================================================
# Limpeza de logs
# ============================================================
LOGS_RETENCAO_DIAS = int(
    _obter_variavel("LOGS_RETENCAO_DIAS", obrigatoria=False, padrao="30")
)
LOGS_TAMANHO_MAXIMO_BYTES = int(
    _obter_variavel("LOGS_TAMANHO_MAXIMO_GB", obrigatoria=False, padrao="10")
) * 1024 * 1024 * 1024

# ============================================================
# Banco de Dados SQLite
# ============================================================
_sqlite_path_str = _obter_variavel("SQLITE_PATH", obrigatoria=False, padrao="")
SQLITE_PATH = (
    Path(_sqlite_path_str) if _sqlite_path_str
    else _RAIZ_PROJETO / "dados" / "backups.db"
)

# ============================================================
# Redis (broker do Celery)
# ============================================================
REDIS_URL = _obter_variavel("REDIS_URL", obrigatoria=False, padrao="redis://localhost:6379/0")
