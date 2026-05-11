# Contexto Operacional — Automação de Backups

Documento de referência para agentes/operadores que vão atuar neste projeto.
Foco: **como operar o sistema em produção**, **fluxo ponta a ponta**, **histórico de incidentes** e **checklist de diagnóstico**.

Última atualização: **2026-04-15**

---

## 1. O que esse projeto faz

Automatiza o backup de contas Google Workspace de colaboradores desligados, disparado por webhook do **Jira Service Management**.

Fluxo resumido:
1. Jira abre um ticket filho em `SPN` quando há desligamento em `ACCESS`
2. Webhook `POST /webhook/backup-desligado` é recebido pelo Flask/Gunicorn
3. Servidor enfileira a task no Celery (broker Redis) e retorna **HTTP 200 imediatamente**
4. Worker Celery (`--pool=threads --concurrency=10`) executa 8 etapas:
   1. **Notificação Jira** — comentário de início + transição de status
   2. **Criar Exportações** — Vault E-mail + Vault Drive
   3. **Monitorar Exportações** — polling até `COMPLETED` (pode durar horas)
   4. **Baixar Arquivos** — streaming do Google Cloud Storage
   5. **Compactar ZIP** — gera SHA256
   6. **Upload Drive** — resumable upload para Shared Drive "MM - Tech - ITO - Backups"
   7. **Atualizar Jira** — comentário de sucesso + transição final
   8. **Excluir Conta** — *somente após confirmação do backup no Drive*

Estado persistido em **SQLite WAL** em `/app/storage/backups.db` (volume Docker `storage_data`).

---

## 2. Onde tudo está

### Servidor de Produção
- **IP:** `10.100.210.200`
- **Usuário:** `infra`
- **Hostname:** `mcta-automacao-bkp`
- **Senha:** **nunca versionada neste repositório** (CLOUD.md). Procurar em um destes locais, nesta ordem:
  1. `~/.credentials` — arquivo local, fora do projeto, modo `0600`. Seção `# Servidor 10.100.210.200`, variável `SSH_PASSWORD`.
  2. `.env` na raiz deste projeto — variável `SSH_PASSWORD_PROD` (arquivo já listado no `.gitignore`).
  3. Se nenhum existir, solicitar ao responsável de infraestrutura. **Não** colar a senha em chats, tickets, commits ou docs versionados.
- **SSH não-interativo (Linux/WSL) — usando `~/.credentials`:**
  ```bash
  # Extrai a senha da seção do servidor 10.100.210.200 sem expô-la em logs:
  export SSHPASS="$(awk '/^# Servidor 10\.100\.210\.200/{flag=1; next} /^# /{flag=0} flag && /^SSH_PASSWORD=/{sub(/^SSH_PASSWORD=/, ""); print; exit}' ~/.credentials)"
  sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 \
    infra@10.100.210.200 '<comando>'
  unset SSHPASS
  ```
- **SSH não-interativo via Python (`paramiko`):** ver `docs/AMBIENTES.md` seção 3.3.

### Infraestrutura
- **VM Proxmox** com 8 GB RAM, 2 discos virtuais:
  - `/dev/sda3` → 80 GB SSD montado em `/` (OS + Docker + SQLite + Redis)
  - `/dev/sdb1` → 4 TB HDD montado em `/mnt/hdd/vault` (downloads do Vault)
- **Permissões críticas:** `/mnt/hdd/vault` deve ser `chown infra:infra` (UID 1000 == `appuser` do container)

### Código e Compose em Produção
- Projeto: `/opt/automacao-backups/`
- Compose ativo: `/opt/automacao-backups/docker-compose.yml` (raiz — NÃO o de `deploy/`)
- Dockerfile: `/opt/automacao-backups/Dockerfile` (raiz)
- Env: `/opt/automacao-backups/.env`
- Credenciais Google: `/opt/automacao-backups/config/credenciais/service-account.json`

### Serviços (containers)
| Container | Porta | Role |
|-----------|-------|------|
| `automacao-backups-servidor-1` | 5000 | Flask/Gunicorn — webhook + dashboard |
| `automacao-backups-worker-1`   | —    | Celery threads — executa backups |
| `automacao-backups-redis-1`    | 6379 | Broker Celery |

### Volumes Docker
- `automacao-backups_storage_data` → `/app/storage` (SQLite)
- `automacao-backups_logs_data` → `/app/logs`
- `automacao-backups_temp_data` → `/app/temp`
- `automacao-backups_redis_data` → `/data`
- Bind mount: `/mnt/hdd/vault:/mnt/hdd/vault` (PASTA_VAULT)

### Ambiente Local de Desenvolvimento
- Caminho: `/home/ivancampos/Projetos/ProjetosITO/automacao-backups/`
- **Máquina local tem proxy Netskope** — requer `SSL_CERT_FILE` e `REQUESTS_CA_BUNDLE`
- **Produção NÃO tem Netskope** — `.env` e `docker-compose.yml` de produção não referenciam `ca-bundle.crt`
- O código compartilhado permanece compatível com Netskope (lê `REQUESTS_CA_BUNDLE` se estiver definido)

---

## 3. Estrutura do código (quem chama quem)

```
app/
  servidor.py          # Entrada Flask (Gunicorn aponta aqui)
  webhook_handler.py   # POST /webhook/backup-desligado
  dashboard.py         # UI de monitoramento
worker/
  celery_app.py        # Configuração Celery + signal handlers
  tarefas.py           # @app.task executar_backup → orquestrador
processamento/
  orquestrador.py      # Fluxo das 8 etapas (função executar_backup_direto)
  compactacao.py       # ZIP + SHA256
  rastreador.py        # Interface sobre dados/ (lazy import)
  recuperacao.py       # Recupera backups interrompidos no startup
  limpeza.py           # Rotação de logs
servicos/
  vault_exportacao.py  # Google Vault: criar/monitorar/baixar
  drive_upload.py      # Upload resumível p/ Shared Drive
  google_auth.py       # Service Account + Domain-Wide Delegation
  jira_atualizacao.py  # Comentários + transições Jira
  google_chat.py       # Webhooks de notificação
  conta_exclusao.py    # Exclusão do usuário no Workspace
dados/
  banco.py             # Conexão SQLite thread-local
  repositorio_backups.py  # CRUD de backups/etapas
config/
  configuracoes.py     # Carrega .env + define PASTA_VAULT/PASTA_TEMP/etc.
utils/
  logger.py retry.py excecoes.py validacoes.py
```

### Cadeia de uma task
```
Webhook Flask → enfileira → Redis → Celery worker
  → worker/tarefas.py::executar_backup
    → processamento/orquestrador.py::executar_backup_direto
      → processamento/rastreador.py (lazy import de dados.repositorio_backups)
      → servicos/vault_exportacao.py (etapas 2/3/4)
      → processamento/compactacao.py (etapa 5)
      → servicos/drive_upload.py (etapa 6)
      → servicos/jira_atualizacao.py (etapas 1/7)
      → servicos/conta_exclusao.py (etapa 8, se deletar_conta=True)
```

---

## 4. Variáveis de ambiente críticas (produção)

Definidas em `docker-compose.yml` (sobrescrevem `.env`):

```yaml
REDIS_URL: redis://redis:6379/0
SQLITE_PATH: /app/storage/backups.db
PASTA_LOGS: /app/logs
PASTA_TEMP: /app/temp
PASTA_VAULT: /mnt/hdd/vault
PYTHONPATH: /app          # ← CRÍTICO, ver Incidente 2026-04-14 #2
```

O `.env` traz:
- `GOOGLE_CREDENCIAIS_PATH=/app/config/credenciais/service-account.json`
- `GOOGLE_ADMIN_EMAIL=backup@madeiramadeira.com.br`
- `VAULT_MATTER_ID=...`
- `DRIVE_PASTA_DESTINO_ID=0AE3Q4dWfJIhAUk9PVA` (Shared Drive backups)
- `JIRA_URL_BASE=https://madeiramadeira.atlassian.net`
- `JIRA_EMAIL=ivan.campos@madeiramadeira.com.br`
- `JIRA_API_TOKEN=...`
- `JIRA_TRANSICAO_EM_ANALISE=501`, `JIRA_TRANSICAO_RESOLVIDO=381`
- `JIRA_CLOUD_ID=c52b487a-f294-4cb9-a67e-3b8983ddfeab`
- `GOOGLE_CHAT_WEBHOOK_URL=...` (espaço de notificação)
- `MAX_EXPORTS_SIMULTANEOS=18` (limite Google = 20, margem de 2)
- `POLLING_INTERVALO_SEGUNDOS=60`, `TIMEOUT_MAXIMO_SEGUNDOS=86400`

---

## 5. Histórico de incidentes e correções

### 2026-04-14 — Downloads silenciosamente presos
**Sintoma:** `source.read(_TAMANHO_CHUNK)` em `vault_exportacao.py` travava indefinidamente após uma queda de conexão com GCS. Arquivos ficavam em 24–188 KB e nunca progrediam.

**Causa raiz:** `blob.open("rb", chunk_size=…)` não tem `timeout` por padrão. Quando a conexão HTTP estagna, a leitura bloqueia sem exceção.

**Correção:** adicionar `timeout` em `baixar_exportacao`:
```python
# servicos/vault_exportacao.py
blob.reload(timeout=60)
with blob.open("rb", chunk_size=_TAMANHO_CHUNK, timeout=300) as source:
```

### 2026-04-14 — Netskope em produção (falso positivo)
**Sintoma:** `.env` e compose de produção referenciavam `ca-bundle.crt` do Netskope, mas o servidor não tem proxy corporativo.

**Correção (produção):** remover `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` do `.env` e do `docker-compose.yml`, e apagar `config/credenciais/ca-bundle.crt`.
**Local (máquina do dev):** permanece com Netskope — código continua env-var-conditional (lê `REQUESTS_CA_BUNDLE` se setado).

### 2026-04-14 — Worker iniciava task e parava sem log
**Sintoma:** Logs mostravam `Tarefa Celery iniciada` → `INÍCIO DO BACKUP` → silêncio. Warning oculto: `No module named 'dados'`.

**Causa raiz:** Celery, quando usa `--logfile`, muda o CWD para `/`. O `''` em `sys.path` passa a apontar para `/` em vez de `/app`, e imports **lazy** de `from dados.repositorio_backups import …` quebram.

**Correção:** adicionar `PYTHONPATH: /app` no `environment` do servidor e do worker no `docker-compose.yml`.

### 2026-04-14 — `PermissionError: [Errno 13]` em `/mnt/hdd/vault`
**Sintoma:** Após subir o worker recriado, primeira task falhava ao `mkdir` em `/mnt/hdd/vault/<email>_<timestamp>`.

**Causa raiz:** Após remount/reboot, o ponto de montagem `/mnt/hdd/vault` voltou com owner `root:root`. Container roda como `appuser` (UID 1000 = `infra` no host).

**Correção (host, como root):**
```bash
sudo chown -R 1000:1000 /mnt/hdd/vault
```

### 2026-04-15 — Containerd em crash-loop + systemd travado
**Sintoma:** `docker exec` e `docker compose up` crashavam o `containerd` com `runtime: s.allocCount != s.nelems && freeIndex == s.nelems`. Após restart do daemon, a próxima operação derrubava de novo. `sudo reboot` e `sudo shutdown -r now` retornavam `Call to Reboot failed: Connection timed out` — o systemd/DBus estava engasgado.

**Hipótese:** Corrupção de memória na VM (`dmesg` mostrou segfaults em `landscape-sysin` e `libcrypto.so.3`).

**Correção (reboot de emergência via kernel):**
```bash
sudo bash -c "echo 1 > /proc/sys/kernel/sysrq && echo b > /proc/sysrq-trigger"
```
Magic SysRq força o kernel a reiniciar sem passar pelo systemd. Use isso apenas quando `reboot`/`shutdown` falharem.

**Observação pós-reboot:** em alguns casos o `docker.service` demora a levantar após o boot (depende de `docker.socket`). Se `docker ps` retornar `Cannot connect to the Docker daemon`, rodar:
```bash
sudo systemctl start docker.socket && sudo systemctl start docker
```

---

## 6. Comandos úteis (copy-paste)

### Ver status dos containers
> Pré-requisito: `SSHPASS` exportado a partir de `~/.credentials` (seção 2 — Servidor de Produção). **Nunca colar a senha no comando.**
```bash
sshpass -e ssh -o StrictHostKeyChecking=no infra@10.100.210.200 \
  'docker ps --format "table {{.Names}}\t{{.Status}}"'
```

### Logs em tempo real
```bash
# Worker (onde os backups rodam)
docker logs -f automacao-backups-worker-1

# Log detalhado do Celery
docker exec automacao-backups-worker-1 tail -f /app/logs/celery_worker.log

# Servidor (webhook + dashboard)
docker logs -f automacao-backups-servidor-1
```

### Consultar banco SQLite (dentro do container)
```bash
docker cp script.py automacao-backups-servidor-1:/tmp/script.py
docker exec automacao-backups-servidor-1 python3 /tmp/script.py
```

Schema:
- `backups` (id, email, ticket_id, nome, status_geral, inicio, fim, link_drive, sha256_zip, erro_mensagem, celery_task_id, deletar_conta)
- `etapas_backup` (id, backup_id, numero, nome, descricao, status, inicio, fim, progresso_pct)
- Status: `em_andamento`, `concluido`, `erro`

### Banco SQLite diretamente no host (fallback quando docker exec não responde)
```bash
sudo ls /var/lib/docker/volumes/automacao-backups_storage_data/_data/
# backups.db backups.db-shm backups.db-wal
```

### Rebuild + subir containers
```bash
cd /opt/automacao-backups
docker compose build           # sem --no-cache para reaproveitar layers
docker compose up -d --force-recreate servidor worker
```
> **Não use `--no-cache` sem necessidade:** pip install em VM com pouca memória crasha com SIGSEGV.

### Reiniciar sem recriar (mais leve, mantém config)
```bash
docker restart automacao-backups-worker-1 automacao-backups-servidor-1
```

### Se containerd/docker travarem
```bash
sudo systemctl stop docker docker.socket
sudo pkill -9 containerd-shim containerd
sleep 3
sudo systemctl start containerd
sleep 5
sudo systemctl start docker
```
Se o crash reaparecer ao primeiro `docker exec`, **faça reboot da VM**: `sudo reboot`.

### Limpar backups travados no banco
```python
# script.py
import sqlite3
conn = sqlite3.connect("/app/storage/backups.db")
cur = conn.cursor()
cur.execute("DELETE FROM etapas_backup WHERE backup_id IN (SELECT id FROM backups WHERE status_geral IN ('erro','em_andamento'))")
cur.execute("DELETE FROM backups WHERE status_geral IN ('erro','em_andamento')")
conn.commit()
```

### Limpar vault no HDD
```bash
sudo rm -rf /mnt/hdd/vault/<email>_<timestamp>
```

---

## 7. Checklist de diagnóstico quando "backups não estão sendo iniciados"

1. **Containers de pé?** `docker ps` — servidor, worker e redis devem estar Up.
2. **Redis respondendo?** `docker exec automacao-backups-redis-1 redis-cli ping` → `PONG`.
3. **Webhook enfileira?** Logs do servidor devem ter `Backup enfileirado no Celery — Task ID: …`.
4. **Worker recebe?** `/app/logs/celery_worker.log` deve mostrar `Task tarefas.executar_backup[...] received`.
5. **Task registra no banco?** Logs devem ter `Backup registrado: <email>`. Se não → provável `ModuleNotFoundError: dados` → cheque `PYTHONPATH`.
6. **Pasta do colaborador criada?** Se falhar com `PermissionError` → `chown 1000:1000 /mnt/hdd/vault`.
7. **Export Vault criado?** Logs mostram `[ETAPA 2/8]`. Se travar aqui, cheque quota Google (limite 20 exports simultâneos) e semáforo (`MAX_EXPORTS_SIMULTANEOS`).
8. **Download progredindo?** Logs mostram `Progresso do upload: X%` / tamanho do arquivo em `/mnt/hdd/vault/<email>_*/` aumentando. Se estagnar → cheque timeouts em `vault_exportacao.py`.

---

## 8. Regras de ouro (extraídas de feedbacks do usuário)

- **NUNCA deletar arquivos de backup do usuário nem contas Google sem confirmação explícita.** Registros internos no SQLite (controle) podem ser limpos quando apropriado.
- **NUNCA ajustar `.env`/compose local sem confirmar** — a máquina do Ivan tem Netskope e precisa das variáveis SSL.
- **NUNCA usar produção como ambiente de testes.** Rebuild sempre revisado.
- **Idioma PT-BR** em toda comunicação, docs, comentários e commits.
- **Concurrency=10** é intencional (10+ desligamentos simultâneos). Pool `threads` é necessário para compartilhar o semáforo do Vault dentro do processo.
- **SSD** guarda SQLite + Redis (I/O aleatório). **HDD** guarda só downloads do Vault (I/O sequencial).

---

## 9. Ponto de trabalho atual

**Data:** 2026-04-15

Fixes já aplicados no código e config de produção (`/opt/automacao-backups/`):
- `servicos/vault_exportacao.py`: `blob.reload(timeout=60)` e `blob.open(..., timeout=300)` — resolve o bug de download silenciosamente preso.
- `servicos/google_auth.py`, `servicos/drive_upload.py`, `config/configuracoes.py`: Netskope removido.
- `.env` e `docker-compose.yml` (raiz): `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE` removidos.
- `docker-compose.yml` (raiz): `PYTHONPATH=/app` adicionado em `environment` do servidor e do worker (sem isso, o worker falha com `No module named 'dados'`).
- `config/credenciais/ca-bundle.crt`: deletado.

**Passos para estabilizar (ordem):**
1. Aguardar a VM voltar após o SysRq reboot.
2. Se `docker ps` retornar `Cannot connect to the Docker daemon`:
   ```bash
   sudo systemctl start docker.socket && sudo systemctl start docker
   ```
3. Reaplicar chown no HDD (necessário sempre após reboot):
   ```bash
   sudo chown -R 1000:1000 /mnt/hdd/vault
   ```
4. Limpar registros `em_andamento`/`erro` no SQLite (todos órfãos das sessões anteriores) — usar script em `/tmp/limpar_db.py` no container do servidor.
5. Validar que o worker está pronto:
   ```bash
   docker exec automacao-backups-worker-1 env | grep PYTHONPATH
   docker exec automacao-backups-worker-1 python3 -c "from dados.repositorio_backups import inserir_backup; print('OK')"
   docker exec automacao-backups-worker-1 sh -c "touch /mnt/hdd/vault/t && rm /mnt/hdd/vault/t && echo OK"
   ```
6. Re-enfileirar `luiz.caleffi@bulkylog.com.br` (SPN-61526) via dashboard — backup nunca chegou a executar, nenhuma exportação criada no Vault.

**Se containerd voltar a crashar:** há evidência de corrupção de memória na VM (segfaults em libcrypto, landscape-sysin, Go runtime panics). Escalar para Proxmox/infra para verificar a VM. Workaround temporário é o SysRq reboot.

