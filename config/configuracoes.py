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

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Carrega o arquivo .env que fica na raiz do projeto (um nível acima de /config)
_RAIZ_PROJETO = Path(__file__).resolve().parent.parent
load_dotenv(_RAIZ_PROJETO / ".env")


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
PASTA_LOGS = _RAIZ_PROJETO / "logs"
PASTA_TEMP = _RAIZ_PROJETO / "temp"

# Cria as pastas se não existirem
PASTA_LOGS.mkdir(exist_ok=True)
PASTA_TEMP.mkdir(exist_ok=True)

# ============================================================
# Google — Service Account e delegação de domínio
# ============================================================
GOOGLE_CREDENCIAIS_PATH = _obter_variavel("GOOGLE_CREDENCIAIS_PATH")
GOOGLE_ADMIN_EMAIL = _obter_variavel("GOOGLE_ADMIN_EMAIL")

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

# ============================================================
# Servidor Flask
# ============================================================
SERVIDOR_PORTA = int(_obter_variavel("SERVIDOR_PORTA", obrigatoria=False, padrao="5000"))
SERVIDOR_HOST = _obter_variavel("SERVIDOR_HOST", obrigatoria=False, padrao="0.0.0.0")

# ============================================================
# Limites de processamento
# ============================================================
# Intervalo entre verificações de status do export (segundos)
POLLING_INTERVALO_SEGUNDOS = int(
    _obter_variavel("POLLING_INTERVALO_SEGUNDOS", obrigatoria=False, padrao="60")
)

# Tempo máximo de espera para um export completar (segundos) — padrão 4 horas
TIMEOUT_MAXIMO_SEGUNDOS = int(
    _obter_variavel("TIMEOUT_MAXIMO_SEGUNDOS", obrigatoria=False, padrao="14400")
)

# Número máximo de exports simultâneos (limite do Google é 20, usamos 18 por segurança)
MAX_EXPORTS_SIMULTANEOS = int(
    _obter_variavel("MAX_EXPORTS_SIMULTANEOS", obrigatoria=False, padrao="18")
)
