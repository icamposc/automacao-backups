# Ambientes e Acesso — Automação de Backups

Documento operacional sobre **o que é o sistema**, **quais são os ambientes**, **como acessá-los** e **como promover mudanças** de DEV para PROD.

Última atualização: 2026-05-08

---

## 1. O que é o sistema

Sistema responsável por executar **backups automáticos** das contas Google Workspace de colaboradores desligados, disparado por webhook do Jira Service Management.

Fluxo resumido (8 etapas, executadas em background pelo Celery):

1. Notificar Jira do início do backup
2. Criar exportações no Google Vault (E-mail e Drive)
3. Monitorar exportações até `COMPLETED`
4. Baixar arquivos exportados do Google Cloud Storage
5. Compactar tudo em ZIP + calcular SHA256
6. Enviar ZIP para o Shared Drive de backups
7. Atualizar ticket no Jira
8. Excluir conta Google Workspace (somente após confirmar o backup no Drive)

Stack: Python 3.12 / Flask + Gunicorn / Celery / Redis / SQLite (WAL) / Docker Compose / Portainer.

Documentos relacionados:
- `docs/CONTEXTO-OPERACIONAL.md` — fluxo detalhado, histórico de incidentes, checklist de diagnóstico
- `docs/configuracao-producao.md` — configuração técnica de produção
- `docs/arquitetura-onpremise.md` — arquitetura de infraestrutura
- `docs/requisitos-jira-webhook.md` — contrato do webhook
- `Pendencias.MD` — itens em análise / decisões pendentes

---

## 2. Ambientes

| Ambiente | Onde | Função |
|---|---|---|
| **DEV / Homologação** | WSL local (este projeto) | Desenvolver e validar mudanças antes de promover para produção |
| **PROD** | VM `10.100.210.200` | Sistema em operação, processa backups reais |

> **Política (CLOUD.md):** *Não ignore o ambiente de homologação — toda mudança deve passar por ele antes de ir para produção.*

### 2.1 DEV — WSL local

| Item | Valor |
|---|---|
| Caminho do projeto | `/home/ivancampos/Projetos/ProjetosITO/automacao-backups` |
| Repositório | `github.com/icamposc/automacao-backups` (privado) |
| Branch padrão | `master` |
| Branches de trabalho | `fix/<tema>` (ex.: `fix/vault-monitoramento-resiliente`) |
| Compose dev | `docker-compose.yml` (raiz) — usa `build: .` |
| Venv local | `venv/` (Python 3.12) |
| Testes | `python -m pytest` |

Subir o stack localmente:

```bash
cd /home/ivancampos/Projetos/ProjetosITO/automacao-backups
docker compose up -d --build
docker compose logs -f worker
docker compose down
```

> **Atenção:** o compose de raiz monta `/mnt/hdd/vault` como bind mount. No WSL essa pasta não existe — criar localmente antes:
> ```bash
> sudo mkdir -p /mnt/hdd/vault
> sudo chown 1000:1000 /mnt/hdd/vault
> ```
> Para testes que não precisam do disco real do servidor de prod, pode-se trocar por um path temporário no próprio docker-compose (ex.: `./.tmp/vault:/mnt/hdd/vault`) — **sem versionar essa alteração**.

### 2.2 PROD — Servidor 10.100.210.200

| Item | Valor |
|---|---|
| IP | `10.100.210.200` |
| Hostname | `mcta-automacao-bkp` |
| Usuário SSH | `infra` |
| Caminho do projeto | `/opt/automacao-backups/` |
| Compose ativo | `/opt/automacao-backups/docker-compose.yml` (raiz, **não** o de `deploy/`) |
| Gestão | Portainer em `https://10.100.210.200:9443` |
| Disco SSD (`/`) | OS + Docker + SQLite + Redis |
| Disco HDD (`/mnt/hdd`) | XFS, exports do Vault e ZIPs |
| Webhook | `POST http://10.100.210.200:5000/webhook/backup-desligado` |
| Health | `GET http://10.100.210.200:5000/health` |

Containers em execução:

| Container | Função |
|---|---|
| `automacao-backups-servidor-1` | Flask + Gunicorn (porta 5000) |
| `automacao-backups-worker-1` | Celery (`--pool=threads --concurrency=N`) |
| `automacao-backups-redis-1` | Broker Celery |
| `portainer` | Gestão de containers (`:9443`) |

### 2.3 Host Proxmox — 10.100.210.211

A VM da automação roda dentro de um host Proxmox.

| Item | Valor |
|---|---|
| IP | `10.100.210.211` |
| Usuário SSH | `root` |
| VMID da automação | `100` (`MCTA-AUTOMACAO-BKP`) |
| Hard reset da VM | `qm reset 100` |
| Status da VM | `qm status 100` |
| Configuração da VM | `qm config 100` |

> Hard reset só após autorização explícita — operação destrutiva (interrompe todos os backups em andamento).

---

## 3. Acesso SSH

### 3.1 Onde estão as credenciais

As senhas SSH **nunca** são versionadas. Ficam em `~/.credentials` (modo `0600`), com seções por servidor:

```
# Servidor 10.100.210.211
SSH_USER=root
SSH_PASSWORD=<senha>

# Servidor 10.100.210.200
SSH_USER=infra
SSH_PASSWORD=<senha>
```

> Em caso de exposição acidental da senha (commit, log, ticket), **reportar imediatamente ao responsável de segurança** e revogar/rotacionar conforme CLOUD.md (seção *Incidentes*).

### 3.2 SSH não-interativo via `sshpass` (Linux/WSL)

`sshpass` está disponível no WSL. Comando-padrão:

```bash
SSHPASS="$(grep '^SSH_PASSWORD=' ~/.credentials | head -1 | cut -d= -f2-)" \
  sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 \
  infra@10.100.210.200 "<comando>"
```

> Atenção: o `~/.credentials` tem múltiplas seções com a mesma chave `SSH_PASSWORD=`. Selecionar a seção correta antes de extrair (ou usar variáveis distintas por servidor).

### 3.3 SSH via Python paramiko

Alternativa quando `sshpass` não está disponível:

```python
import paramiko
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(
    "10.100.210.200", port=22, username="infra", password=SENHA,
    timeout=30, banner_timeout=30, auth_timeout=30,
)
stdin, stdout, stderr = client.exec_command("<comando>", timeout=20)
```

---

## 4. Comandos úteis em produção

```bash
# Status dos containers
docker ps

# Logs (seguir)
docker logs -f automacao-backups-servidor-1
docker logs -f automacao-backups-worker-1

# Logs combinados da aplicação
docker exec automacao-backups-worker-1 tail -f /app/logs/automacao_backups.log

# Estado do banco (backups recentes)
docker exec automacao-backups-servidor-1 python3 -c "
import sqlite3
c = sqlite3.connect('/app/storage/backups.db').cursor()
c.execute('SELECT id, email, ticket_id, status_geral, inicio FROM backups ORDER BY id DESC LIMIT 10')
for r in c.fetchall(): print(r)
"

# Espaço em disco
df -h / /mnt/hdd

# Healthcheck do servidor
curl -fsS http://localhost:5000/health

# Ping no Celery
docker exec -w /app automacao-backups-worker-1 \
  celery -A worker.celery_app inspect ping --timeout 5

# Tamanho do vault
du -sh /mnt/hdd/vault/
```

---

## 5. Fluxo de promoção DEV → PROD

Sequência obrigatória para qualquer mudança que vá para produção:

1. **Branch** — criar branch a partir de `master` (ou da branch de tema ativa) com nome descritivo: `fix/<tema>` ou `feat/<tema>`. Ex.: `fix/download-watchdog`, `fix/compactacao-stored`.
2. **Implementar e testar localmente (WSL)**
   - Rodar `python -m pytest` (testes unitários precisam passar)
   - Subir o stack via `docker compose up -d --build`
   - Validar a mudança no comportamento esperado (ver `docs/CONTEXTO-OPERACIONAL.md` para cenários)
3. **Pull Request**
   - PR pequeno e focado (uma correção ou pequeno conjunto coeso)
   - Descrição com: o que muda, por que, como testar, riscos
   - Aprovação de outro membro da equipe (CLOUD.md exige revisão antes do merge)
   - Testes automatizados verdes
4. **Merge em `master`**
5. **Deploy em PROD** — passos manuais no servidor `10.100.210.200`:
   ```bash
   ssh infra@10.100.210.200
   cd /opt/automacao-backups
   git pull origin master
   docker compose build
   docker compose up -d
   docker compose logs -f worker     # validar inicialização
   curl -fsS http://localhost:5000/health
   ```
6. **Validação pós-deploy** — verificar que:
   - Containers estão `Up` e `healthy`
   - Não há backups travados (`status_geral='em_andamento'` há mais de algumas horas)
   - Logs sem erros novos
7. **Rollback** (se necessário) — reverter o commit em `master` ou usar a tag/SHA anterior:
   ```bash
   cd /opt/automacao-backups && git checkout <sha-anterior>
   docker compose build && docker compose up -d
   ```

> **Drift conhecido (a alinhar):** o `deploy/docker-compose.yml` do repositório descreve uma configuração diferente da que está rodando em prod (workers/concurrency e healthchecks). O compose efetivamente em uso em prod é o `docker-compose.yml` da raiz. Antes de qualquer mudança estrutural no compose, alinhar a divergência.

---

## 6. Política e cuidados (CLOUD.md)

- **Idioma:** toda comunicação, documentação, commits, PRs e tickets em **PT-BR**.
- **Credenciais:** nunca commitar arquivos `.env`, `service-account.json`, senhas. Usar `~/.credentials` localmente. Verificar `.gitignore` antes de adicionar arquivos.
- **Branches:** nunca commit direto em `master`. Sempre via PR com revisão.
- **Force push:** proibido em branches compartilhadas.
- **Mudanças em produção:** sempre passar por homologação (este WSL) antes. Toda execução em produção exige autorização explícita.
- **Incidentes:** suspeita de vazamento de credenciais → reportar imediatamente.
- **Comunicação:** em caso de dúvida sobre requisitos, credenciais ou contexto — perguntar antes de agir.

---

## 7. Pontos de atenção atuais

- **Histórico recente de incidentes I/O em `/mnt/hdd`** (jbd2 em 20/04, deadlock XFS em 22/04 e 08/05). Causa raiz aparente: HDD físico saturado pela concorrência atual de download. Detalhes em `docs/CONTEXTO-OPERACIONAL.md`.
- **Bug conhecido:** `acks_late=True` + `pool=threads` pode gerar re-delivery storm em reconexões Redis. Mitigação parcial: deduplicação por status no DB.
- **Recovery cego:** `recuperacao.py` re-enfileira qualquer backup interrompido sem checar histórico de falhas. Pode entrar em loop de reprocessamento se a causa raiz persistir.
- **Pendência de segurança:** ver `Pendencias.MD` item 3 — limpeza de histórico Git e rotação de credenciais, conforme CLOUD.md.
