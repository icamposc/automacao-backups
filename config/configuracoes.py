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
# NAS Synology — Destino primário do backup (pull pelo NAS)
# ============================================================
# O servidor MOVE o ZIP finalizado para NAS_SYNC_DIR/<email>/<arquivo>.zip.
# O NAS Synology sincroniza essa pasta por conta propria (sem markers). O ZIP
# local e apagado na finalizacao (apos a janela NAS_SYNC_HORAS_ESPERA); a
# varredura limpar_zips_sincronizados (boot) cobre orfaos com mais de
# NAS_SYNC_RETENCAO_HORAS, baseada na idade do proprio .zip.
#
# PROVISIONAMENTO EM PRODUCAO (10.100.80.10):
#   sudo mkdir -p /mnt/hdd/vault/sync_nas
#   sudo chown 1000:1000 /mnt/hdd/vault/sync_nas
#   sudo chmod 770 /mnt/hdd/vault/sync_nas
# IMPORTANTE: a pasta DEVE ficar dentro de /mnt/hdd/vault (XFS, /dev/sdb1, ~3 TB),
# MESMO filesystem dos ZIPs em /mnt/hdd/vault/zips — assim o move do ZIP e
# instantaneo (os.rename). /mnt/hdd (sem /vault) e o root LVM de ~28 GB; usar
# /mnt/hdd/sync_nas la encheria o root e faria copia lenta entre filesystems.
# Em DEV (WSL) basta apontar NAS_SYNC_DIR no .env para uma pasta local
# gravavel (ex: ./dados/sync_nas).
_nas_sync_dir_env = os.getenv("NAS_SYNC_DIR", "")
NAS_SYNC_DIR = Path(_nas_sync_dir_env) if _nas_sync_dir_env else Path("/mnt/hdd/vault/sync_nas")

# Cria a pasta apenas se ja for possivel (DEV com path local), sem quebrar o
# import quando o caminho padrao /mnt/hdd nao existe em ambientes que ainda
# nao provisionaram o disco. Em PROD a pasta deve existir antes do container
# subir (vide PROVISIONAMENTO acima); em DEV o usuario deve sobrescrever
# NAS_SYNC_DIR no .env.
try:
    NAS_SYNC_DIR.mkdir(parents=True, exist_ok=True)
except (PermissionError, FileNotFoundError):
    # Silencioso: o nas_sync.py tenta criar a subpasta por email no uso real;
    # se ainda falhar la, o fallback Drive captura via ErroNasSync.
    pass

NAS_SYNC_RETENCAO_HORAS = int(
    _obter_variavel("NAS_SYNC_RETENCAO_HORAS", obrigatoria=False, padrao="6")
)

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
# Google Chat — Webhooks para notificações
# ============================================================
# Chat principal — fluxo operacional (início, progresso, sucesso, conta excluída).
GOOGLE_CHAT_WEBHOOK_URL = _obter_variavel("GOOGLE_CHAT_WEBHOOK_URL", obrigatoria=False, padrao="")

# Chat de LOGS — erros técnicos, falhas, bloqueios e alertas de saúde do sistema.
# Aponta para o grupo "LOG - Automação Backups". Se ficar vazio, esses alertas
# usam o webhook principal como fallback (preserva visibilidade).
GOOGLE_CHAT_WEBHOOK_URL_LOGS = _obter_variavel(
    "GOOGLE_CHAT_WEBHOOK_URL_LOGS", obrigatoria=False, padrao=""
)

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

# Limite de backups em execução simultânea — deve refletir o --concurrency do
# worker Celery (deploy/docker-compose.yml). Usado apenas para exibição no
# dashboard (ativos / limite). Default 9 = teto do semáforo do Vault
# (MAX_EXPORTS_SIMULTANEOS=18 ÷ 2 exports por backup). Configurável via env
# para não voltar a divergir do --concurrency.
LIMITE_PARALELO_BACKUPS = int(
    _obter_variavel("LIMITE_PARALELO_BACKUPS", obrigatoria=False, padrao="9")
)

# Threads paralelas no download de arquivos do Cloud Storage por backup.
# Padrão 2 = otimizado para HDD rotacional (PASTA_VAULT em /mnt/hdd):
# acima disso o iostat mostra %util=100% com r/s=w/s=0 (contenção na
# fila do controlador virtio). Em NVMe esse limite some — pode subir
# para 6 ou 8. Configurável via env para permitir ajuste sem mudança
# de código quando o storage for migrado.
DOWNLOAD_MAX_WORKERS = int(
    _obter_variavel("DOWNLOAD_MAX_WORKERS", obrigatoria=False, padrao="2")
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

# ============================================================
# Autenticação no Active Directory (login do dashboard)
# ============================================================
# O acesso ao /dashboard e às rotas /api/backups/* exige login no AD.
# A autenticação é por bind direto com a credencial do próprio usuário
# (via UPN) e o acesso é restrito aos membros do grupo AD_GRUPO_AUTORIZADO.
#
# Estas variáveis NÃO têm valor real padrão (devem ser preenchidas no .env).
# São opcionais na carga para não quebrar testes/health quando o AD não está
# configurado — a ausência é validada apenas no momento de um login real.

# Alvo LDAP. Com vários DCs do mesmo domínio (HA), prefira o NOME DO DOMÍNIO
# (ex.: "madeiramadeira.local") — o DNS resolve para todos os DCs. Também
# aceita uma LISTA separada por vírgula de DCs (FQDN/IP), que vira um pool com
# failover. Evite IP único: se aquele DC cair, o login para.
AD_SERVIDOR = _obter_variavel("AD_SERVIDOR", obrigatoria=False, padrao="")

# Porta LDAP. Padrão 636 (LDAPS). Use 389 para LDAP sem TLS.
AD_PORTA = int(_obter_variavel("AD_PORTA", obrigatoria=False, padrao="636"))

# Usar LDAPS (TLS). "true"/"false". Padrão true (recomendado).
AD_USAR_SSL = _obter_variavel("AD_USAR_SSL", obrigatoria=False, padrao="true").strip().lower() in (
    "true", "1", "sim", "on", "yes"
)

# Validar o certificado do servidor AD. "true"/"false". Padrão true.
# Defina "false" apenas se o controlador usa certificado autoassinado e não
# há como confiar na CA corporativa no contêiner.
AD_VALIDAR_CERT = _obter_variavel("AD_VALIDAR_CERT", obrigatoria=False, padrao="true").strip().lower() in (
    "true", "1", "sim", "on", "yes"
)

# Sufixo UPN do domínio, usado para montar usuario@dominio no bind.
# Ex.: "madeiramadeira.com.br"
AD_DOMINIO_UPN = _obter_variavel("AD_DOMINIO_UPN", obrigatoria=False, padrao="")

# DN base para buscar o usuário e o grupo. Ex.: "DC=madeiramadeira,DC=com,DC=br"
AD_BASE_DN = _obter_variavel("AD_BASE_DN", obrigatoria=False, padrao="")

# Nome (CN) do grupo do AD cujos membros têm acesso ao dashboard.
AD_GRUPO_AUTORIZADO = _obter_variavel(
    "AD_GRUPO_AUTORIZADO", obrigatoria=False, padrao="MM - Backup - Admins"
)

# Timeout (segundos) para conectar ao servidor AD.
AD_TIMEOUT = int(_obter_variavel("AD_TIMEOUT", obrigatoria=False, padrao="10"))

# ============================================================
# Usuário administrador LOCAL (break-glass, sem AD)
# ============================================================
# Conta de emergência para acessar o painel quando o AD estiver indisponível
# ou antes de o login corporativo estar configurado. NÃO usa AD: valida
# usuário + senha localmente. A senha é guardada apenas como HASH (Werkzeug),
# nunca em texto puro. Gere o hash com:
#   python -c "from werkzeug.security import generate_password_hash as g; print(g('SUA_SENHA'))"
#
# O login local só fica ATIVO se as DUAS variáveis estiverem preenchidas.
# Use um nome que NÃO exista no AD (ex.: admin.local) para evitar colisão.
ADM_LOCAL_USUARIO = _obter_variavel("ADM_LOCAL_USUARIO", obrigatoria=False, padrao="")
ADM_LOCAL_SENHA_HASH = _obter_variavel("ADM_LOCAL_SENHA_HASH", obrigatoria=False, padrao="")

# ============================================================
# Sessão do Flask (cookie de login)
# ============================================================
# Chave usada para assinar o cookie de sessão. DEVE ser fixa e secreta em
# produção: o Gunicorn roda com --workers 2 e ambos precisam compartilhar a
# mesma chave, senão o login cai a cada troca de worker. Se vazia, o servidor
# gera uma chave aleatória no startup (apenas para dev/teste — sessões não
# sobrevivem a reinício nem são compartilhadas entre workers).
FLASK_SECRET_KEY = _obter_variavel("FLASK_SECRET_KEY", obrigatoria=False, padrao="")

# Enviar o cookie de sessão apenas por HTTPS. "true"/"false". Padrão false
# (acesso interno por HTTP). Defina "true" quando o dashboard estiver atrás
# de HTTPS/proxy TLS.
SESSION_COOKIE_SECURE = _obter_variavel(
    "SESSION_COOKIE_SECURE", obrigatoria=False, padrao="false"
).strip().lower() in ("true", "1", "sim", "on", "yes")

# Tempo de vida da sessão de login, em horas. Padrão 8 (uma jornada).
SESSION_HORAS = int(_obter_variavel("SESSION_HORAS", obrigatoria=False, padrao="8"))
