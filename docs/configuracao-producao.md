# Configuração de Produção — Automação de Backups

## Infraestrutura

| Item | Detalhe |
|---|---|
| Servidor | On-premise com Proxmox |
| VM | Ubuntu Server 24.04 LTS |
| IP | 10.100.210.200/24 |
| Disco SSD | 80GB — OS + Docker + SQLite + Redis |
| Disco HDD | 4TB (`/dev/sdb1`) — montado em `/mnt/hdd` |
| Pasta Vault | `/mnt/hdd/vault` — exports PST/ZIP do Google Vault |

---

## Docker

Imagem buildada localmente no servidor:

```bash
cd /opt/automacao-backups
docker build -t automacao-backups:latest .
```

Gerenciado via **Portainer** (`https://10.100.210.200:9443`).

---

## Containers

| Container | Imagem | Função |
|---|---|---|
| `automacao-backups-redis-1` | `redis:7-alpine` | Broker Celery |
| `automacao-backups-servidor-1` | `automacao-backups:latest` | Flask + Gunicorn (porta 5000) |
| `automacao-backups-worker-1` | `automacao-backups:latest` | Celery 10 threads |

---

## Volumes

| Volume | Tipo | Caminho no Host | Uso |
|---|---|---|---|
| `storage_data` | Named (Docker) | Gerenciado pelo Docker (SSD) | SQLite |
| `logs_data` | Named (Docker) | Gerenciado pelo Docker (SSD) | Logs da aplicação |
| `temp_data` | Named (Docker) | Gerenciado pelo Docker (SSD) | Arquivos temporários |
| `redis_data` | Named (Docker) | Gerenciado pelo Docker (SSD) | Persistência Redis |
| `/mnt/hdd/vault` | Bind mount | `/mnt/hdd/vault` (HDD 4TB) | Exports PST/ZIP Vault |
| `config/credenciais` | Bind mount | `/opt/automacao-backups/config/credenciais` | Service Account Google |

---

## Variáveis de ambiente sobrescritas pelo docker-compose

Estas variáveis são definidas no `docker-compose.yml` e sobrescrevem o `.env`:

| Variável | Valor em Produção |
|---|---|
| `REDIS_URL` | `redis://redis:6379/0` |
| `SQLITE_PATH` | `/app/storage/backups.db` |
| `PASTA_LOGS` | `/app/logs` |
| `PASTA_TEMP` | `/app/temp` |
| `PASTA_VAULT` | `/mnt/hdd/vault` |

---

## Comandos úteis no servidor

```bash
# Ver status dos containers
docker ps

# Logs do worker
docker logs automacao-backups-worker-1 --tail 50 -f

# Logs do servidor
docker logs automacao-backups-servidor-1 --tail 50 -f

# Health check
curl http://10.100.210.200:5000/health

# Rebuild e redeploy após atualização de código
cd /opt/automacao-backups
docker build -t automacao-backups:latest .
docker compose up -d --force-recreate servidor worker
```

---

## Ambiente de Homologação (WSL)

Para rodar localmente sem Docker:

```bash
# 1. Redis (já instalado no WSL)
sudo service redis-server start

# 2. Servidor Flask (Terminal 1)
cd /home/ivancampos/Projetos/ProjetosITO/automacao-backups
source venv/bin/activate
gunicorn --bind 0.0.0.0:5000 --workers 2 --timeout 120 \
  --access-logfile logs/gunicorn_acesso.log \
  --error-logfile logs/gunicorn_erro.log \
  --log-level info app.servidor:app

# 3. Worker Celery (Terminal 2)
cd /home/ivancampos/Projetos/ProjetosITO/automacao-backups
source venv/bin/activate
celery -A worker.celery_app worker \
  --pool=threads --concurrency=10 --loglevel=info
```

Diferenças do `.env` em homologação (já configuradas):

| Variável | Homologação | Produção |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` | `redis://redis:6379/0` (docker-compose) |
| `PASTA_VAULT` | `./dados/vault` | `/mnt/hdd/vault` (docker-compose) |
| `PASTA_LOGS` | `./logs` | `/app/logs` (docker-compose) |
