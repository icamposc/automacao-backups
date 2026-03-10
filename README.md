# Automacao de Backups

Sistema de automacao de backup de dados de colaboradores desligados, integrado ao **Jira Service Management** via webhook.

Exporta e-mails (Gmail/PST) e arquivos (Google Drive) atraves do **Google Vault**, compacta em ZIP, faz upload para um **Drive Compartilhado** e exclui a conta do Google Workspace apos confirmacao do backup — atualizando o ticket Jira em cada etapa do processo.

---

## Indice

- [Arquitetura](#arquitetura)
- [Pre-requisitos](#pre-requisitos)
- [Instalacao](#instalacao)
- [Configuracao](#configuracao)
- [Como Executar](#como-executar)
- [Rotas da API](#rotas-da-api)
- [Testes](#testes)
- [Estrutura do Projeto](#estrutura-do-projeto)
- [Tecnologias](#tecnologias)
- [Contribuicao](#contribuicao)

---

## Arquitetura

### Fluxo de Processamento

```
                        ┌──────────────────────┐
                        │  Jira Service Mgmt   │
                        │  (Webhook POST)      │
                        └──────────┬───────────┘
                                   │
                                   ▼
                        ┌──────────────────────┐
                        │  Validacao            │
                        │  • HMAC-SHA256        │
                        │  • Payload            │
                        │  • Duplicatas         │
                        └──────────┬───────────┘
                                   │
                          HTTP 200 │ (resposta imediata)
                                   │
                                   ▼
                     ┌─────────────────────────────────┐
                     │   Thread de Background           │
                     │                                  │
                     │  1. Notificar Jira (inicio)      │
                     │  2. Criar exports no Vault       │
                     │  3. Monitorar exports             │
                     │  4. Baixar do Cloud Storage       │
                     │  5. Compactar em ZIP              │
                     │  6. Upload → Drive Compartilhado  │
                     │  7. Atualizar Jira (link)         │
                     │  8. Verificar backup + Excluir conta │
                     │                                  │
                     └──────────────┬───────────────────┘
                                    │
                                    ▼
                          Limpeza de temporarios
```

### Decisoes Tecnicas

| Decisao | Justificativa |
|---|---|
| **Processamento assincrono** | Webhook retorna HTTP 200 imediatamente; backup roda em thread de background para nao bloquear o servidor |
| **Semaforo de concorrencia** | Maximo de 18 exports simultaneos (limite do Google Vault: 20), evitando throttling |
| **Monitoramento em paralelo** | Exports de e-mail e Drive sao monitorados em threads paralelas via `ThreadPoolExecutor` |
| **Upload resumivel** | Suporta retentativas automaticas para arquivos grandes |
| **Tolerancia a falhas** | Erros no Jira nao bloqueiam o backup; limpeza garantida via `finally` |
| **Exclusao segura de conta** | Conta so e excluida apos verificacao (status 200) de que o backup existe no Drive Compartilhado |

---

## Pre-requisitos

- **Python** 3.10 ou superior
- **Google Workspace** com acesso a:
  - Google Vault (Matter pre-configurado)
  - Google Drive (Shared Drive)
  - Cloud Storage (leitura dos exports)
  - Admin SDK / Directory API (exclusao de contas)
- **Service Account** com Domain-Wide Delegation habilitada e os escopos:
  - `ediscovery` — exportacoes no Vault
  - `devstorage.read_only` — download dos exports
  - `drive` — upload para Drive Compartilhado
  - `admin.directory.user` — exclusao de contas do Workspace
- **Jira Service Management** com API token ativo
- **Gunicorn** (incluido nas dependencias, para producao)

---

## Instalacao

```bash
# 1. Clonar o repositorio
git clone git@github.com:icamposc/automacao-backups.git
cd automacao-backups

# 2. Criar e ativar o ambiente virtual
python3 -m venv venv
source venv/bin/activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Configurar variaveis de ambiente
cp .env.example .env
```

> **Importante:** nunca versione o arquivo `.env`. Ele ja esta no `.gitignore`.

---

## Configuracao

Edite o arquivo `.env` com os valores do seu ambiente:

### Google

| Variavel | Descricao | Exemplo |
|---|---|---|
| `GOOGLE_CREDENCIAIS_PATH` | Caminho para o JSON da Service Account | `config/credenciais/service-account.json` |
| `GOOGLE_ADMIN_EMAIL` | E-mail do admin para delegacao de dominio | `admin@empresa.com` |
| `VAULT_MATTER_ID` | ID do Matter no Google Vault | `abc123def456` |
| `DRIVE_PASTA_DESTINO_ID` | ID da pasta no Drive Compartilhado | `1A2B3C4D5E6F` |

### Jira

| Variavel | Descricao | Exemplo |
|---|---|---|
| `JIRA_URL_BASE` | URL base da instancia Jira | `https://empresa.atlassian.net` |
| `JIRA_EMAIL` | E-mail da conta de automacao | `automacao@empresa.com` |
| `JIRA_API_TOKEN` | Token de API ([gerar aqui](https://id.atlassian.com/manage-profile/security/api-tokens)) | `token-aqui` |
| `JIRA_WEBHOOK_SEGREDO` | Segredo para validacao HMAC-SHA256 | `segredo-do-webhook` |

### Servidor

| Variavel | Descricao | Padrao |
|---|---|---|
| `SERVIDOR_HOST` | Host do servidor | `0.0.0.0` |
| `SERVIDOR_PORTA` | Porta do servidor | `5000` |

### Limites de Processamento

| Variavel | Descricao | Padrao |
|---|---|---|
| `POLLING_INTERVALO_SEGUNDOS` | Intervalo entre verificacoes de status do export | `180` |
| `TIMEOUT_MAXIMO_SEGUNDOS` | Tempo maximo de espera por export (segundos) | `21600` (6h) |
| `MAX_EXPORTS_SIMULTANEOS` | Exports simultaneos no Vault (limite Google: 20) | `18` |

### Credenciais da Service Account

Coloque o arquivo JSON da Service Account em `config/credenciais/`. Esta pasta ja esta no `.gitignore`.

---

## Como Executar

### Desenvolvimento

```bash
./scripts/iniciar_servidor.sh --dev
```

- Debug habilitado
- Recarrega automaticamente ao alterar codigo
- Servidor em `http://localhost:5000`

### Producao

```bash
./scripts/iniciar_servidor.sh
```

- Gunicorn com 2 workers
- Logs de acesso e erro em `logs/`
- Timeout de 120s por requisicao

---

## Rotas da API

### `POST /webhook/backup-desligado`

Recebe o webhook do Jira e inicia o backup em background.

**Headers:**
| Header | Obrigatorio | Descricao |
|---|---|---|
| `Content-Type` | Sim | `application/json` |
| `X-Hub-Signature` | Nao | Assinatura HMAC-SHA256 para validacao |

**Body:**
```json
{
  "email_colaborador": "usuario@empresa.com",
  "ticket_id": "SPN-123",
  "nome_colaborador": "Nome do Colaborador"
}
```

| Campo | Tipo | Obrigatorio | Descricao |
|---|---|---|---|
| `email_colaborador` | string | Sim | E-mail do colaborador desligado |
| `ticket_id` | string | Sim | Chave do ticket no Jira |
| `nome_colaborador` | string | Nao | Nome (usado em logs e comentarios) |

**Respostas:**

| Codigo | Descricao |
|---|---|
| `200` | Backup iniciado (ou ja em processamento) |
| `400` | Payload invalido |
| `401` | Assinatura do webhook invalida |
| `500` | Erro interno |

---

### `GET /saude`

Health check para monitoramento (PRTG, Zabbix, etc).

```json
{
  "status": "ok",
  "servico": "automacao-backups",
  "versao": "1.0.0"
}
```

---

### `GET /`

Informacoes gerais do servico e rotas disponiveis.

---

## Testes

### Testes Unitarios

```bash
pytest testes/ -v
```

### Simulacao Manual de Webhook

```bash
python -m testes.simular_webhook usuario@empresa.com SPN-123 "Nome do Colaborador"
```

Envia uma requisicao POST para o servidor local simulando o webhook do Jira.

---

## Estrutura do Projeto

```
automacao-backups/
│
├── app/                            # Camada de apresentacao (API)
│   ├── servidor.py                 #   Servidor Flask e rotas HTTP
│   ├── webhook_handler.py          #   Validacao e extracao do webhook
│   └── __init__.py
│
├── processamento/                  # Camada de orquestracao
│   ├── orquestrador.py             #   Coordenacao das 8 etapas do backup
│   ├── compactacao.py              #   Compactacao ZIP com verificacao de disco
│   ├── limpeza.py                  #   Remocao de temporarios e ZIPs
│   └── __init__.py
│
├── servicos/                       # Camada de integracao (APIs externas)
│   ├── google_auth.py              #   Autenticacao Google (Service Account)
│   ├── vault_exportacao.py         #   Exportacao, monitoramento e download
│   ├── drive_upload.py             #   Upload resumivel para Drive Compartilhado
│   ├── conta_exclusao.py           #   Verificacao do backup e exclusao de conta
│   ├── jira_atualizacao.py         #   Comentarios e transicoes no Jira
│   └── __init__.py
│
├── utils/                          # Utilitarios transversais
│   ├── logger.py                   #   Logging centralizado (console + arquivo)
│   ├── validacoes.py               #   Validacao de e-mail e payload
│   └── __init__.py
│
├── config/                         # Configuracoes
│   ├── configuracoes.py            #   Carregamento de variaveis de ambiente
│   ├── credenciais/                #   Service Account JSON (gitignore)
│   └── __init__.py
│
├── testes/                         # Testes
│   ├── teste_webhook.py            #   Testes unitarios do webhook
│   ├── teste_vault.py              #   Testes de integracao com Vault
│   ├── teste_drive.py              #   Testes de integracao com Drive
│   └── simular_webhook.py          #   Simulador para testes manuais
│
├── scripts/
│   └── iniciar_servidor.sh         # Script de inicializacao (dev/prod)
│
├── docs/                           # Documentacao tecnica
│   └── webhook-e-mensagens-jira.md #   Payload do webhook e mensagens por etapa
│
├── logs/                           # Logs com rotacao automatica (gitignore)
├── temp/                           # Arquivos temporarios (gitignore)
├── requirements.txt                # Dependencias Python
├── .env.example                    # Modelo de variaveis de ambiente
└── .gitignore
```

---

## Tecnologias

| Tecnologia | Versao | Funcao |
|---|---|---|
| [Flask](https://flask.palletsprojects.com/) | 3.1.0 | Servidor web para receber webhooks |
| [Gunicorn](https://gunicorn.org/) | 23.0.0 | Servidor WSGI para producao |
| [google-api-python-client](https://github.com/googleapis/google-api-python-client) | 2.159.0 | APIs do Google (Vault, Drive) |
| [google-auth](https://github.com/googleapis/google-auth-library-python) | 2.37.0 | Autenticacao com Service Account |
| [google-cloud-storage](https://github.com/googleapis/python-storage) | 2.19.0 | Download dos exports do Vault |
| [requests](https://docs.python-requests.org/) | 2.32.3 | Comunicacao com API REST do Jira |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | 1.0.1 | Carregamento de variaveis de ambiente |

---

## Contribuicao

1. Crie uma branch a partir de `master`:
   ```bash
   git checkout -b feature/minha-alteracao
   ```
2. Faca suas alteracoes e adicione testes quando aplicavel
3. Rode os testes para garantir que nada quebrou:
   ```bash
   pytest testes/ -v
   ```
4. Abra um Pull Request com descricao clara do que foi alterado e por que
5. Aguarde a revisao e aprovacao antes do merge
