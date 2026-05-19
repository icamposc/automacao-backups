# Automação de Backups — ITO

> Última atualização: 2026-05-19 — versão `2.0.0`

Sistema de automação de backup de dados de colaboradores desligados, integrado ao **Jira Service Management** via webhook.

Ao receber o webhook, o sistema exporta os e-mails da caixa Gmail (formato **PST**, via **Google Vault**) e os arquivos do **Google Drive** (formato ZIP), consolida tudo em um único `.zip`, faz upload para um **Drive Compartilhado**, submete o formulário do chamado e fecha o ticket — excluindo a conta do Google Workspace somente após confirmar que o backup está no Drive.

---

## Índice

- [Como Funciona](#como-funciona)
- [Fluxo Completo](#fluxo-completo)
- [Pré-requisitos](#pré-requisitos)
- [Instalação](#instalação)
- [Configuração](#configuração)
- [Docker (produção)](#docker-recomendado-para-produção)
- [Como Executar](#como-executar)
- [Integração com Jira](#integração-com-jira)
- [Rotas da API](#rotas-da-api)
- [Notificações Google Chat](#notificações-google-chat)
- [Dashboard](#dashboard)
- [Testes](#testes)
- [Estrutura do Projeto](#estrutura-do-projeto)
- [Tecnologias](#tecnologias)
- [Contribuição](#contribuição)

---

## Como Funciona

### Contexto

O processo é iniciado por um chamado de desligamento no projeto **ACCESS** do Jira. A automação do Jira cria automaticamente um chamado filho no projeto **SPN** com o título `Backup desligamento - [NOME] - ACCESS-XXXXXX`. Quando esse chamado filho é aberto, o webhook dispara este sistema.

```
ACCESS-XXXXXX (PAI — Desligamento)
    └── SPN-XXXXXX (FILHO — Backup desligamento)  ← webhook dispara aqui
```

### Princípio de Funcionamento

O servidor recebe o webhook e retorna **HTTP 200 imediatamente** — sem bloquear o Jira. A tarefa é enfileirada no **Redis** e processada por um **worker Celery em background**, garantindo que o backup não seja perdido mesmo que o servidor seja reiniciado após o enfileiramento.

---

## Fluxo Completo

```
Jira cria SPN filho
        │
        ▼
POST /webhook/backup-desligado
        │
        ├─ Valida assinatura HMAC (opcional)
        ├─ Extrai e-mail da descrição (regex)
        ├─ Verifica duplicatas
        └─ HTTP 200 ──► Worker Celery (background)
                                │
                                ▼
                    ┌─────────────────────────┐
                    │  ETAPA 1                │
                    │  Jira: comentário +     │
                    │  transição "Em Análise" │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │  ETAPA 2                │
                    │  Vault: criar ou        │
                    │  reaproveitar exports   │
                    │  (E-mail + Drive)       │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │  ETAPA 3                │
                    │  Monitorar exports em   │
                    │  paralelo (até COMPLETED)│
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │  ETAPA 4                │
                    │  Download do            │
                    │  Cloud Storage          │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │  ETAPA 5                │
                    │  Compactar em ZIP       │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │  ETAPA 6                │
                    │  Upload para Drive      │
                    │  Compartilhado          │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │  ETAPA 7                │
                    │  Jira: comentário de    │
                    │  sucesso com link       │
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │  ETAPA 8                │
                    │  Verificar backup no    │
                    │  Drive → excluir conta  │
                    │  → submit formulário    │
                    │  → transição "Resolvido"│
                    └────────────┬────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │  Limpeza de temporários │
                    └─────────────────────────┘
```

### Decisões Técnicas

| Decisão | Justificativa |
|---|---|
| Processamento assíncrono | Webhook retorna HTTP 200 imediatamente; tarefa enfileirada no Redis e processada pelo worker Celery para não bloquear o Jira |
| Semáforo de concorrência | Máximo de 18 exports simultâneos (limite Google Vault: 20) |
| Monitoramento em paralelo | Exports de e-mail e Drive monitorados via `ThreadPoolExecutor` |
| Reaproveitamento de exports | Se exports já existem no Vault (COMPLETED ou IN_PROGRESS), são reutilizados — evita duplicatas em caso de retry |
| Upload chunked via requests | Contorna o problema do Netskope que remove o header `Location` no upload resumível do httplib2 |
| Exclusão segura de conta | Conta só é excluída após verificar (HTTP 200) que o arquivo ZIP existe no Drive Compartilhado |
| Tolerância a falhas no Jira | Erros de comentário/transição são logados mas não interrompem o backup |
| Pre-flight de capacidade de disco | Antes de baixar um export do Vault, o sistema confere o espaço livre em `PASTA_VAULT` contra o tamanho declarado e aborta cedo se não couber, evitando falhas tardias |
| Blacklist de recuperação | Backups que falharam imediatamente após um reinício do servidor não são re-enfileirados em loop pela rotina de recuperação, prevenindo "torrenting" de erros |
| Healthcheck que retorna 503 quando degradado | `/health` responde 503 se o worker Celery não responder a `inspect ping` em 5 s (D-state, deadlock de FS) ou se o disco em `PASTA_VAULT` estiver abaixo do limite mínimo |

---

## Pré-requisitos

- **Python** 3.11 ou superior
- **Redis** (broker e backend do Celery) — `redis-server`
- **Google Workspace** com:
  - Google Vault (Matter pré-configurado)
  - Google Drive Compartilhado (Shared Drive) — pasta de destino criada
  - Google Cloud Storage (acesso de leitura aos exports do Vault)
  - Google Admin SDK / Directory API (para exclusão de contas)
- **Service Account** com Domain-Wide Delegation e escopos:
  - `https://www.googleapis.com/auth/ediscovery`
  - `https://www.googleapis.com/auth/devstorage.read_only`
  - `https://www.googleapis.com/auth/drive`
  - `https://www.googleapis.com/auth/admin.directory.user`
- **Jira Service Management** com API token ativo (conta de serviço dedicada recomendada)
- **Gunicorn** (incluído nas dependências, para produção)

---

## Instalação

```bash
# 1. Clonar o repositório
git clone git@github.com:icamposc/automacao-backups.git
cd automacao-backups

# 2. Criar e ativar o ambiente virtual
python3 -m venv venv
source venv/bin/activate

# 3. Instalar dependências
pip install -r requirements.txt

# 4. Configurar variáveis de ambiente
cp .env.example .env
# Edite o .env com os valores do seu ambiente

# 5. Colocar o JSON da Service Account em:
# config/credenciais/service-account.json
```

> **Importante:** nunca versione o arquivo `.env` nem a pasta `config/credenciais/`. Ambos já estão no `.gitignore`.

---

## Configuração

Edite o arquivo `.env` com os valores do seu ambiente:

### Google

| Variável | Descrição | Exemplo |
|---|---|---|
| `GOOGLE_CREDENCIAIS_PATH` | Caminho para o JSON da Service Account | `config/credenciais/service-account.json` |
| `GOOGLE_ADMIN_EMAIL` | E-mail do admin para delegação de domínio | `admin@empresa.com` |
| `VAULT_MATTER_ID` | ID do Matter no Google Vault | `abc123-def456` |
| `DRIVE_PASTA_DESTINO_ID` | ID da pasta no Drive Compartilhado | `1A2B3C4D5E6F` |

### Jira

| Variável | Descrição | Exemplo |
|---|---|---|
| `JIRA_URL_BASE` | URL base da instância Jira | `https://empresa.atlassian.net` |
| `JIRA_EMAIL` | E-mail da conta de automação | `automacao@empresa.com` |
| `JIRA_API_TOKEN` | Token de API ([gerar aqui](https://id.atlassian.com/manage-profile/security/api-tokens)) | `ATATT3x...` |
| `JIRA_WEBHOOK_SEGREDO` | Segredo para validação HMAC-SHA256 (opcional) | `segredo-do-webhook` |
| `JIRA_CLOUD_ID` | Cloud ID da instância Atlassian (necessário para a API de Formulários) | `c52b487a-...` |
| `JIRA_TRANSICAO_EM_ANALISE` | ID da transição "Em Análise" no projeto SPN | `501` |
| `JIRA_TRANSICAO_RESOLVIDO` | ID da transição "Resolvido" no projeto SPN | `381` |

> Para descobrir os IDs de transição: `GET /rest/api/3/issue/{ticket}/transitions`
>
> Para obter o Cloud ID: `GET https://[instancia].atlassian.net/_edge/tenant_info`

### Google Chat

| Variável | Descrição |
|---|---|
| `GOOGLE_CHAT_WEBHOOK_URL` | URL do webhook do espaço **principal** no Google Chat — notificações operacionais (início, progresso, sucesso). Opcional. |
| `GOOGLE_CHAT_WEBHOOK_URL_LOGS` | URL do webhook do espaço de **logs** — erros técnicos, falhas e alertas de saúde. Se vazio, esses alertas usam o webhook principal como fallback. |

### Servidor

| Variável | Descrição | Padrão |
|---|---|---|
| `SERVIDOR_HOST` | Host do servidor | `0.0.0.0` |
| `SERVIDOR_PORTA` | Porta do servidor | `5000` |

### Limites de Processamento

| Variável | Descrição | Padrão |
|---|---|---|
| `POLLING_INTERVALO_SEGUNDOS` | Intervalo entre verificações de status do export | `60` (1 min) |
| `TIMEOUT_MAXIMO_SEGUNDOS` | Tempo máximo de espera por export | `14400` (4 h) |
| `MAX_EXPORTS_SIMULTANEOS` | Exports simultâneos no Vault (limite Google: 20) | `18` |

### Redis e Worker Celery

| Variável | Descrição | Padrão |
|---|---|---|
| `REDIS_URL` | URL de conexão com o Redis (broker e backend do Celery) | `redis://localhost:6379/0` |

### SSL Corporativo (Netskope / Proxy)

Em redes com proxy que interceptam HTTPS, configure o bundle de certificados:

| Variável | Descrição |
|---|---|
| `SSL_CERT_FILE` | Caminho para o bundle com certs do sistema + corporativo |
| `REQUESTS_CA_BUNDLE` | Mesmo caminho (usado pela biblioteca `requests`) |

```env
SSL_CERT_FILE=/caminho/para/ca-bundle.crt
REQUESTS_CA_BUNDLE=/caminho/para/ca-bundle.crt
```

---

## Docker (recomendado para produção)

O projeto inclui `Dockerfile` e `docker-compose.yml` prontos. Uma única imagem é usada para o servidor e o worker — apenas o comando muda.

> Para deploy em servidor Ubuntu (com Docker em disco dedicado e `data-root` no HDD), veja o guia completo em [docs/instalacao-ubuntu-servidor.md](docs/instalacao-ubuntu-servidor.md).

### Pré-requisitos

- Docker e Docker Compose instalados no servidor
- Arquivo `.env` preenchido (copie de `.env.example`)
- `config/credenciais/service-account.json` presente

### Subir o ambiente completo

```bash
docker compose up -d
```

Isso inicia três containers: `redis`, `servidor` e `worker`.

### Comandos úteis

```bash
# Acompanhar logs em tempo real
docker compose logs -f

# Ver apenas os logs do worker (onde o backup roda)
docker compose logs -f worker

# Parar tudo
docker compose down

# Reconstruir a imagem após atualizar o código
docker compose build && docker compose up -d

# Verificar saúde dos containers
docker compose ps
```

### Volumes criados automaticamente

| Volume | Conteúdo |
|---|---|
| `storage_data` | Banco de dados SQLite (`backups.db`) |
| `logs_data` | Arquivos de log do servidor e do worker |
| `temp_data` | Arquivos temporários durante o processamento |
| `redis_data` | Dados do Redis (fila de tarefas) |

> As credenciais (`config/credenciais/`) são montadas como volume somente leitura — nunca são copiadas para dentro da imagem.

### Observações sobre o .env com Docker

O `docker-compose.yml` sobrescreve automaticamente as variáveis abaixo — **não é necessário alterá-las no `.env`**:

| Variável | Valor no Docker |
|---|---|
| `REDIS_URL` | `redis://redis:6379/0` (rede interna do Compose) |
| `SQLITE_PATH` | `/app/storage/backups.db` (volume dedicado) |
| `PASTA_LOGS` | `/app/logs` (volume `logs_data`) |
| `PASTA_TEMP` | `/app/temp` (volume `temp_data` no SSD) |
| `PASTA_VAULT` | `/mnt/hdd/vault` (bind mount no HDD) |

---

## Como Executar

O sistema é composto por dois processos que devem rodar em paralelo: o **servidor Flask** (recebe webhooks) e o **worker Celery** (processa os backups). O Redis deve estar rodando antes de iniciar qualquer um dos dois.

### Usando os scripts prontos (recomendado)

```bash
# Terminal 1 — Servidor
./scripts/iniciar_servidor.sh           # Produção (Gunicorn)
./scripts/iniciar_servidor.sh --dev     # Desenvolvimento (Flask com reload)

# Terminal 2 — Worker Celery
./scripts/iniciar_worker.sh             # Produção (background, log em arquivo)
./scripts/iniciar_worker.sh --dev       # Desenvolvimento (log no terminal)
```

### Manualmente

```bash
source venv/bin/activate

# 1. Redis (se não estiver rodando)
redis-server &

# 2. Servidor Flask (desenvolvimento)
python -m app.servidor

# 3. Worker Celery (em outro terminal)
#    PYTHONPATH=$PWD é necessário para o Celery encontrar os módulos
#    locais (dados, processamento, servicos etc.) ao rodar fora do Docker.
#    --pool=threads é obrigatório: o semáforo global de exports do Vault
#    é process-local e só funciona com pool de threads.
export PYTHONPATH=$PWD
celery -A worker.celery_app worker --loglevel=info --pool=threads --concurrency=4
```

### Produção (Gunicorn + Celery)

```bash
source venv/bin/activate

# Servidor
gunicorn -w 2 -b 0.0.0.0:5000 --timeout 120 app.servidor:app

# Worker (em outro terminal ou como serviço)
export PYTHONPATH=$PWD
celery -A worker.celery_app worker \
    --loglevel=info --pool=threads --concurrency=4 \
    --logfile=logs/celery_worker.log --detach --pidfile=logs/celery_worker.pid
```

> A `--concurrency=4` reflete o valor usado em produção (Docker Compose). Em ambientes com I/O mais rápido (NVMe), é seguro subir para 8 — observando que o limite de exports paralelos do Vault (`MAX_EXPORTS_SIMULTANEOS=18`) corresponde a ~9 backups simultâneos.

### Verificar se está rodando

```bash
# Health check detalhado (recomendado — usado pelos healthchecks do Compose)
curl http://localhost:5000/health

# Health check simples (alias legado para monitoramento básico tipo PRTG/Zabbix)
curl http://localhost:5000/saude
# {"status": "ok", "servico": "automacao-backups", "versao": "2.0.0"}
```

---

## Integração com Jira

Esta seção descreve o que a **equipe de projetos do Jira** precisa configurar para que o webhook dispare automaticamente.

### Quando disparar

O webhook deve ser disparado quando o **chamado filho SPN** do tipo `Backup desligamento` for **criado** pela automação do Jira.

### Opção A — Recomendada: adicionar à automação existente

Na regra que já cria o chamado SPN a partir do ACCESS, adicionar uma ação logo após o "Create issue":

| Campo | Valor |
|---|---|
| Ação | Send web request |
| URL | `http://[IP-DO-SERVIDOR]:5000/webhook/backup-desligado` |
| Método | `POST` |
| Header | `Content-Type: application/json` |
| Body | `{"descricao": "{{issue.description}}", "ticket_id": "{{issue.key}}"}` |

> `{{issue.key}}` e `{{issue.description}}` devem referenciar o **chamado filho SPN** recém-criado, não o PAI ACCESS.

### Opção B — Automação separada no projeto SPN

| Campo | Valor |
|---|---|
| Trigger | Issue Created |
| Scope | Projeto: SPN |
| Condition | Summary começa com `Backup desligamento` |
| Ação | Send web request (mesma configuração acima) |

### Formato da Descrição

O sistema extrai o e-mail do colaborador via regex a partir da descrição do chamado SPN. O campo **`Email Coorporativo:`** deve estar presente na descrição exatamente com essa grafia:

```
Nome colaborador: NOME COMPLETO
Email Coorporativo: email@empresa.com.br
```

### Campos Preenchidos Automaticamente na Transição "Resolvido"

O sistema preenche os campos obrigatórios da tela de transição automaticamente:

| Campo | Valor |
|---|---|
| Resolução | Done |
| Tipo de atividade | Suporte Dúvidas/Suporte uso incorreto |
| sdn_time | Automação |
| Equipe Resolvedora | Automação |
| Custo de Manutenção | 0 |

> Se o workflow do Jira for alterado e esses campos mudarem, os IDs correspondentes no código precisam ser atualizados.

### API Token do Jira

O sistema precisa de um API token ativo para executar as ações abaixo no Jira:

| Ação | Quando |
|---|---|
| Adicionar comentários | Em cada etapa do processo |
| Transicionar status ("Em Análise") | Início do backup |
| Submeter formulário ("Gerenciamento de Serviços e Servidores") | Antes de fechar o ticket |
| Transicionar status ("Resolvido") | Após exclusão da conta |

**Recomendação:** usar uma **conta de serviço dedicada** (não pessoal) com permissão apenas no projeto SPN, para que o sistema não pare caso a conta pessoal seja desativada.

---

## Rotas da API

### `POST /webhook/backup-desligado`

Recebe o webhook do Jira e inicia o backup em background.

**Headers:**

| Header | Obrigatório | Descrição |
|---|---|---|
| `Content-Type` | Sim | `application/json` |
| `X-Hub-Signature` | Não | Assinatura HMAC-SHA256 para validação |

**Body:**

```json
{
  "descricao": "Dados Colaborador Desligado:Nome colaborador: NOME...Email Coorporativo: email@empresa.com.br...",
  "ticket_id": "SPN-123"
}
```

| Campo | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `descricao` | string | Sim | Texto completo da descrição do chamado (`{{issue.description}}`) |
| `ticket_id` | string | Sim | Chave do chamado no Jira (`{{issue.key}}`) |

**Respostas:**

| Código | Descrição |
|---|---|
| `200` | Backup iniciado com sucesso |
| `200` | Backup já em andamento para este e-mail (ignorado) |
| `400` | Payload inválido ou e-mail não encontrado na descrição |
| `401` | Assinatura HMAC inválida |
| `500` | Erro interno |

---

### `GET /saude`

Health check simples para monitoramento (PRTG, Zabbix, etc.).

```json
{"status": "ok", "servico": "automacao-backups", "versao": "2.0.0"}
```

### `GET /health`

Health check detalhado com status de cada componente.

```json
{
  "status": "ok",
  "versao": "2.0.0",
  "timestamp": "2026-04-13T09:00:00.000000",
  "backups_em_andamento": 1,
  "componentes": {"servidor": "ok", "banco": "ok", "celery": "ok"},
  "resumo": {"ativos": 1, "sucessos": 10, "erros": 2, "total_finalizados": 12},
  "ultima_execucao": {"email": "...", "fim": "...", "status": "concluido"}
}
```

---

### `GET /dashboard`

Dashboard web com acompanhamento em tempo real dos backups em andamento e histórico de execuções.

---

### `GET /api/backups/ativos`

Retorna os backups em andamento com todas as etapas e progresso (JSON).

### `GET /api/backups/historico?pagina=1&por_pagina=50`

Retorna o histórico de backups finalizados com paginação (JSON).

| Param | Tipo | Padrão | Máximo |
|---|---|---|---|
| `pagina` | int | 1 | — |
| `por_pagina` | int | 50 | 200 |

### `GET /api/backups/resumo`

Retorna contadores gerais: total, concluídos, em andamento, com erro (JSON).

### `GET /api/backups/fila`

Retorna o estado vivo da fila Celery: backups em execução, limite paralelo configurado e itens aguardando worker (JSON).

### `GET /api/backups/<email>`

Retorna o backup mais recente (ativo ou finalizado) para o e-mail informado.

### `POST /api/backups/iniciar`

Inicia um backup manualmente sem depender do webhook do Jira.

**Body JSON:**

```json
{
  "email": "colaborador@empresa.com.br",
  "nome": "Nome Completo",
  "ticket_id": "SPN-999"
}
```

| Campo | Obrigatório | Descrição |
|---|---|---|
| `email` | Sim | E-mail corporativo do colaborador |
| `nome` | Não | Nome completo (para notificações e logs) |
| `ticket_id` | Não | Chave do ticket Jira; se omitida, gera `MANUAL-{timestamp}` |

**Respostas:**

| Código | Descrição |
|---|---|
| `200` | Backup enfileirado com sucesso |
| `400` | E-mail inválido ou ausente |
| `409` | Já existe um backup em andamento para este e-mail |

### `GET /api/backups/lote/template`

Faz download de um template CSV com o cabeçalho esperado pelo endpoint de lote (`email,nome,ticket_id`). Útil para preencher e reenviar via `/api/backups/lote`.

### `POST /api/backups/lote`

Recebe um CSV com até **50 e-mails** e enfileira um backup por linha, ignorando duplicatas e e-mails já em processamento.

**Form data:**

| Campo | Tipo | Obrigatório | Descrição |
|---|---|---|---|
| `arquivo` | file (CSV, UTF-8) | Sim | CSV com cabeçalho `email,nome,ticket_id` — limite de 50 linhas |

**Respostas:**

| Código | Descrição |
|---|---|
| `200` | Lote processado — JSON com `enfileirados`, `ignorados` e detalhes por e-mail |
| `400` | CSV ausente, malformado ou acima do limite de 50 linhas |

---

## Notificações Google Chat

O sistema envia cards formatados para o Google Chat em cada evento relevante:

| Evento | Card |
|---|---|
| Backup iniciado | `🔄 Backup Iniciado` |
| Export do Vault reaproveitado | `⚠️ Vault: Exports Existentes Detectados` |
| Backup concluído com sucesso | `✅ Backup Concluído` com link do Drive |
| Conta excluída | `🗑️ Conta Excluída` |
| Erro no processo | `❌ Erro no Backup` com descrição do erro |

> O alerta de **exports reaproveitados** indica que uma execução anterior falhou após criar os exports no Vault. O sistema retoma o processo automaticamente sem recriar os exports.

Para configurar, crie um webhook no Google Chat e adicione a URL em `GOOGLE_CHAT_WEBHOOK_URL` no `.env`.

---

## Dashboard

Acesse `http://[servidor]:5000/dashboard` para visualizar:

- **Backups em andamento** — colaborador, ticket, etapa atual e progresso visual das 8 etapas
- **Progresso em tempo real** — barra e percentual de Download (etapa 4) e Upload (etapa 6), atualizados a cada chunk/arquivo processado
- **Histórico** — backups finalizados com status, link do Drive e SHA-256 do ZIP
- **Contadores** — em andamento, concluídos, com erro e total
- **Disparo manual** — formulário para iniciar um backup sem depender do webhook do Jira
- **Upload em lote via CSV** — envio de planilha com até **50 e-mails** de uma vez; template disponível em `/api/backups/lote/template`
- **Auto-refresh** — dados atualizados automaticamente a cada 10 segundos

---

## Testes

### Testes Unitários

A suíte cobre cerca de **98 funções em 11 arquivos** `test_*.py`, incluindo webhook, orquestrador, compactação, integrações Google (Vault/Drive/Chat), rastreador, recuperação, retry e performance.

```bash
source venv/bin/activate

# Rodar toda a suíte
pytest testes/ -v

# Com relatório de cobertura (pytest-cov já faz parte das dependências de teste)
pytest testes/ --cov=. --cov-report=term-missing
```

### Simular Webhook Manualmente

Com o servidor rodando, envie um webhook de teste via Python:

```python
import hmac, hashlib, json, requests

payload = {
    "descricao": "Dados Colaborador Desligado:Nome colaborador: NOME TESTEEmail Coorporativo: teste@empresa.com.br",
    "ticket_id": "SPN-999"
}
corpo = json.dumps(payload, ensure_ascii=False).encode("utf-8")
segredo = "segredo-do-webhook"
assinatura = hmac.new(segredo.encode(), corpo, hashlib.sha256).hexdigest()

requests.post(
    "http://localhost:5000/webhook/backup-desligado",
    data=corpo,
    headers={"Content-Type": "application/json", "X-Hub-Signature": assinatura}
)
```

---

## Estrutura do Projeto

```
automacao-backups/
│
├── app/                            # Camada de apresentação (API)
│   ├── servidor.py                 #   Servidor Flask e rotas HTTP
│   ├── webhook_handler.py          #   Validação, extração e autenticação do webhook
│   ├── dashboard.py                #   Rotas e renderização do dashboard web
│   └── templates/                  #   Templates HTML do dashboard
│
├── worker/                         # Processamento assíncrono (Celery + Redis)
│   ├── celery_app.py               #   Configuração da instância Celery
│   └── tarefas.py                  #   Task Celery que executa o fluxo de backup
│
├── processamento/                  # Orquestração do fluxo
│   ├── orquestrador.py             #   Coordena as 8 etapas do backup
│   ├── compactacao.py              #   Compactação ZIP com verificação de espaço
│   ├── limpeza.py                  #   Remoção de temporários e ZIPs
│   ├── rastreador.py               #   Interface pública de rastreamento (delega para repositorio_backups)
│   ├── recuperacao.py              #   Retoma backups interrompidos no startup (com blacklist)
│   ├── saude.py                    #   Coleta de métricas de saúde (worker, disco, fila)
│   └── __init__.py
│
├── servicos/                       # Integrações com APIs externas
│   ├── google_auth.py              #   Autenticação Google (Service Account + DWD)
│   ├── vault_exportacao.py         #   Criação, reaproveitamento, monitoramento e download
│   ├── drive_upload.py             #   Upload chunked via requests (compatível com Netskope)
│   ├── conta_exclusao.py           #   Verificação do backup e exclusão da conta Workspace
│   ├── jira_atualizacao.py         #   Comentários, transições e submissão de formulários
│   ├── google_chat.py              #   Notificações e alertas via webhook do Chat
│   └── __init__.py
│
├── dados/                          # Persistência (SQLite + WAL mode)
│   ├── banco.py                    #   Inicialização, migrações e marcação de backups interrompidos no restart
│   └── repositorio_backups.py      #   CRUD das tabelas backups e etapas_backup (inclui progresso_pct)
│
├── utils/                          # Utilitários transversais
│   ├── logger.py                   #   Logging centralizado (console + arquivo rotativo)
│   ├── validacoes.py               #   Validação de payload e extração de e-mail/nome via regex
│   ├── excecoes.py                 #   Exceções personalizadas por etapa do backup
│   ├── retry.py                    #   Cálculo de backoff exponencial para retentativas
│   └── __init__.py
│
├── config/                         # Configuração
│   ├── configuracoes.py            #   Carregamento e validação de variáveis de ambiente
│   ├── credenciais/                #   Service Account JSON + CA bundle (gitignore)
│   └── __init__.py
│
├── scripts/                        # Scripts de inicialização
│   ├── iniciar_servidor.sh         #   Inicia o servidor (Gunicorn ou Flask --dev)
│   └── iniciar_worker.sh           #   Inicia o worker Celery (produção ou --dev)
│
├── testes/                         # Testes (pytest)
│   ├── conftest.py                 #   Fixtures compartilhadas do pytest
│   ├── test_webhook_handler.py     #   Testes do handler de webhook
│   ├── test_servidor.py            #   Testes das rotas Flask (webhook, health, dashboard)
│   ├── test_orquestrador.py        #   Testes do orquestrador
│   ├── test_compactacao.py         #   Testes de compactação ZIP
│   ├── test_vault_exportacao.py    #   Testes de integração com Google Vault
│   ├── test_drive_upload.py        #   Testes de upload para Google Drive
│   ├── test_google_chat.py         #   Testes de notificação no Google Chat
│   ├── test_rastreador.py          #   Testes do rastreador de estado
│   ├── test_recuperacao.py         #   Testes da rotina de recuperação de backups interrompidos
│   ├── test_retry.py               #   Testes do mecanismo de retry (backoff exponencial)
│   ├── test_performance.py         #   Benchmarks e simulações de carga (I/O, compactação)
│   └── simular_webhook.py          #   Simulador para testes manuais
│
├── deploy/                         # Artefatos de produção
│   ├── Dockerfile                  #   Imagem usada em produção (Portainer)
│   └── docker-compose.yml          #   Compose com paths absolutos do servidor de produção
│
├── docs/                           # Documentação técnica
│   ├── instalacao-ubuntu-servidor.md  # Passo-a-passo de deploy em Ubuntu 26.04 (Docker no sdb1)
│   ├── requisitos-jira-webhook.md  #   Requisitos da integração com Jira
│   ├── webhook-e-mensagens-jira.md #   Payload e mensagens por etapa
│   ├── arquitetura-onpremise.md    #   Diagrama e descrição da arquitetura on-premise
│   ├── configuracao-producao.md    #   Detalhes de infraestrutura, volumes e variáveis em produção
│   ├── CONTEXTO-OPERACIONAL.md     #   Guia operacional (incidentes, diagnóstico, checklist)
│   └── paper-tco-infraestrutura-backup.md  # Análise de TCO (rascunho)
│
├── logs/                           # Logs com rotação automática (gitignore)
├── temp/                           # Arquivos temporários do processamento (gitignore)
├── Dockerfile                      # Imagem para dev/homologação (Python 3.11-slim)
├── docker-compose.yml              # Compose para dev/homologação (volumes nomeados)
├── requirements.txt                # Dependências Python
├── pytest.ini                      # Configuração do pytest
├── .env.example                    # Modelo de variáveis de ambiente
└── .gitignore
```

---

## Tecnologias

| Tecnologia | Versão | Função |
|---|---|---|
| [Flask](https://flask.palletsprojects.com/) | 3.1.0 | Servidor web para receber webhooks |
| [Gunicorn](https://gunicorn.org/) | 23.0.0 | Servidor WSGI para produção |
| [Celery](https://docs.celeryq.dev/) | 5.4.0 | Processamento assíncrono de backups |
| [Redis](https://redis.io/) | — | Broker e backend do Celery |
| [google-api-python-client](https://github.com/googleapis/google-api-python-client) | 2.159.0 | APIs do Google (Vault, Drive, Admin SDK) |
| [google-auth](https://github.com/googleapis/google-auth-library-python) | 2.37.0 | Autenticação com Service Account |
| [google-auth-httplib2](https://github.com/googleapis/google-auth-library-python-httplib2) | 0.3.0 | Transporte HTTP com suporte a CA bundle customizado |
| [google-cloud-storage](https://github.com/googleapis/python-storage) | 2.19.0 | Download dos exports do Vault |
| [requests](https://docs.python-requests.org/) | 2.32.3 | Upload Drive + chamadas à API REST do Jira |
| [httplib2](https://github.com/httplib2/httplib2) | 0.31.2 | Transporte HTTP das APIs Google |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | 1.0.1 | Carregamento de variáveis de ambiente |

---

## Contribuição

1. Crie uma branch a partir de `master`:
   ```bash
   git checkout -b feature/minha-alteracao
   ```
2. Faça suas alterações e adicione testes quando aplicável
3. Rode os testes antes de abrir o PR:
   ```bash
   pytest testes/ -v
   ```
4. Abra um Pull Request com descrição clara do que foi alterado e por quê
5. Aguarde revisão e aprovação antes do merge

> Nunca faça commit direto na branch `master`.
