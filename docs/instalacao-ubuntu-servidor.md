# Instalação em Servidor Ubuntu 26.04 LTS

> Última atualização: 2026-05-19

Guia passo-a-passo para provisionar o serviço `automacao-backups` em um servidor Ubuntu com dois discos físicos, separando carga pesada (downloads e uploads de PSTs/ZIPs do Vault) em um disco dedicado.

---

## Visão geral do layout

O projeto separa duas categorias de armazenamento:

| Disco | Sistema de arquivos | Mount | O que armazena |
|---|---|---|---|
| `sda` | ext4 sobre LVM | `/` | Apenas SO Ubuntu, código do projeto e `.env` |
| `sdb1` | XFS | `/mnt/hdd/vault` | **Tudo do Docker** (imagens, containers, named volumes) **e** os exports do Vault (PSTs/ZIPs baixados, ZIP consolidado) |

```
sda3 (LVM ext4, ~26 GB total)             sdb1 (XFS, 3,2 TB+)
└── /                                     └── /mnt/hdd/vault/
    ├── /opt/automacao-backups/               ├── docker/                    ← data-root do Docker
    │   ├── .env (chmod 600)                  │   ├── image/
    │   ├── config/credenciais/               │   ├── containers/
    │   │   └── service-account.json          │   ├── overlay2/
    │   ├── docker-compose.yml                │   └── volumes/
    │   └── (código clonado)                  │       ├── automacao-backups_storage_data/
    └── (SO Ubuntu + journald)                │       ├── automacao-backups_logs_data/
                                              │       ├── automacao-backups_redis_data/
                                              │       └── automacao-backups_temp_data/
                                              ├── {email}_{ts}/email/        ← PSTs baixados do Vault
                                              ├── {email}_{ts}/drive/        ← ZIPs do Drive baixados do Vault
                                              └── zips/{email}_{ts}.zip      ← ZIP consolidado pronto para upload
```

**Por que esse layout?**

- O `/` tem apenas ~19 GB livres. Manter imagens + named volumes + logs (até 10 GB) deixaria a margem em ~7 GB, frágil sob qualquer pico.
- Movendo o `data-root` do Docker para o sdb1, o `/` fica permanentemente folgado e todo o crescimento natural (logs, imagens novas, builds) é absorvido pelo sdb1 (3 TB).
- O bind mount `/mnt/hdd/vault:/mnt/hdd/vault` no `docker-compose.yml` continua mapeando exports/zips do pipeline para o mesmo disco — sem editar o compose.

---

## Pré-requisitos

- Ubuntu Server 26.04 LTS instalado (mínimo: 2 vCPU, 4 GB RAM, sda 50 GB, sdb 500 GB)
- Acesso SSH com usuário sudoer
- sdb1 já formatado como XFS e montado em `/mnt/hdd/vault` (este guia assume isso)
- JSON da Service Account com Domain-Wide Delegation (será copiado via `scp`)

> Confirme com `lsblk -f` antes de começar. A esperada é uma linha do tipo:
> ```
> sdb1   xfs   <UUID>   <ESPACO_LIVRE>   <%>   /mnt/hdd/vault
> ```

---

## Fase 1 — Sistema operacional base

```bash
sudo apt update && sudo apt -y full-upgrade
sudo apt install -y curl ca-certificates gnupg lsb-release ufw jq git
sudo timedatectl set-timezone America/Sao_Paulo
```

---

## Fase 2 — Robustecer o sdb1 (sem reformatar)

Acrescenta `nofail` (evita o boot travar se o HDD falhar), `noatime` (menos escrita) e timeout do systemd. Cria as pastas necessárias com as permissões corretas.

```bash
grep /mnt/hdd/vault /etc/fstab
sudo cp /etc/fstab /etc/fstab.bak-$(date +%F)
UUID_SDB=$(sudo blkid -s UUID -o value /dev/sdb1)
sudo sed -i -E "s|(UUID=${UUID_SDB}[[:space:]]+/mnt/hdd/vault[[:space:]]+xfs[[:space:]]+)[^[:space:]]+|\\1defaults,nofail,noatime,x-systemd.device-timeout=10|" /etc/fstab
sudo systemctl daemon-reload
sudo mount -o remount /mnt/hdd/vault
sudo mkdir -p /mnt/hdd/vault/zips /mnt/hdd/vault/docker
sudo chown -R 1000:1000 /mnt/hdd/vault/zips
sudo chown root:root /mnt/hdd/vault/docker
sudo chmod 711 /mnt/hdd/vault/docker
ls -la /mnt/hdd/vault
```

**Por quê dessas permissões:**
- `/mnt/hdd/vault/zips` → UID 1000 (usuário do container — definido no `Dockerfile`).
- `/mnt/hdd/vault/docker` → `root:root` com `711`, idêntico ao default do Docker em `/var/lib/docker`.

---

## Fase 3 — Instalar Docker (sem iniciar ainda)

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl stop docker docker.socket
```

> Se o repositório `docker.com` ainda não tiver build para o codename do 26.04, trocar `$(lsb_release -cs)` por `noble`. Pacotes da 24.04 são compatíveis com a 26.04.

---

## Fase 4 — Apontar Docker para o sdb1

Configura `data-root` no `/etc/docker/daemon.json` **antes** de iniciar o daemon (evita ter que parar e mover arquivos depois).

```bash
sudo mkdir -p /etc/docker
printf '{\n  "data-root": "/mnt/hdd/vault/docker",\n  "storage-driver": "overlay2",\n  "log-driver": "json-file",\n  "log-opts": {\n    "max-size": "50m",\n    "max-file": "5"\n  }\n}\n' | sudo tee /etc/docker/daemon.json >/dev/null
sudo cat /etc/docker/daemon.json
```

**O que cada opção faz:**

| Opção | Efeito |
|---|---|
| `data-root` | Tudo do Docker (imagens, containers, named volumes) passa a viver em `/mnt/hdd/vault/docker` em vez de `/var/lib/docker` |
| `storage-driver: overlay2` | Driver padrão sobre ext4/xfs, declarado explicitamente para evitar fallback |
| `log-opts max-size/max-file` | Rotação de logs por container: 5 × 50 MB. Sem isso, o JSON driver default cresce indefinidamente |

---

## Fase 5 — Override systemd: Docker depende do mount

Sem isso, se o sdb1 falhar no boot, o Docker cria silenciosamente `/mnt/hdd/vault/docker` no `/` (sda) e perde a separação.

```bash
sudo mkdir -p /etc/systemd/system/docker.service.d
printf '[Unit]\nRequires=mnt-hdd-vault.mount\nAfter=mnt-hdd-vault.mount\n' | sudo tee /etc/systemd/system/docker.service.d/override.conf >/dev/null
sudo systemctl daemon-reload
systemctl list-units --type=mount | grep -i hdd
```

> Nome da unit (`mnt-hdd-vault.mount`) é derivado automaticamente pelo systemd a partir do ponto de montagem `/mnt/hdd/vault`.

---

## Fase 6 — Iniciar Docker e validar layout

```bash
sudo systemctl enable --now docker
sudo docker info | grep -E 'Docker Root Dir|Storage Driver|Logging Driver'
sudo docker run --rm hello-world
ls -la /mnt/hdd/vault/docker/
sudo usermod -aG docker $USER
```

A saída do `docker info` deve mostrar:
```
Storage Driver: overlay2
Logging Driver: json-file
Docker Root Dir: /mnt/hdd/vault/docker
```

> Faça **logout/login** (ou `newgrp docker`) depois do `usermod` para o grupo entrar em vigor sem `sudo`.

---

## Fase 7 — Clonar o projeto no sda

```bash
sudo mkdir -p /opt/automacao-backups && sudo chown 1000:1000 /opt/automacao-backups
cd /opt/automacao-backups && git clone <URL_DO_REPO> .
mkdir -p config/credenciais
# Copie o service-account.json via scp da sua estação:
#   scp ~/seguro/service-account.json usuario@servidor:/opt/automacao-backups/config/credenciais/
chmod 600 config/credenciais/service-account.json
cp .env.example .env && chmod 600 .env
nano .env
```

### Variáveis obrigatórias no `.env`

| Grupo | Variáveis |
|---|---|
| Google | `GOOGLE_ADMIN_EMAIL`, `VAULT_MATTER_ID`, `DRIVE_PASTA_DESTINO_ID` |
| Jira | `JIRA_URL_BASE`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_WEBHOOK_SEGREDO`, `JIRA_CLOUD_ID`, `JIRA_TRANSICAO_EM_ANALISE`, `JIRA_TRANSICAO_RESOLVIDO` |
| Chat | `GOOGLE_CHAT_WEBHOOK_URL`, `GOOGLE_CHAT_WEBHOOK_URL_LOGS` |

### Variáveis a **não** preencher

O `docker-compose.yml` sobrescreve automaticamente:
- `REDIS_URL`, `SQLITE_PATH`, `PASTA_LOGS`, `PASTA_TEMP`, `PASTA_VAULT`

---

## Fase 8 — Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow 5000/tcp comment 'automacao-backups Flask'
sudo ufw --force enable
sudo ufw status numbered
```

---

## Fase 9 — Subir os containers

```bash
cd /opt/automacao-backups
docker compose build --pull
docker compose up -d
docker compose ps
```

Esperado: 3 containers `Up (healthy)` — `redis`, `servidor`, `worker`.

---

## Fase 10 — Verificação ponta a ponta

```bash
df -h / /mnt/hdd/vault
curl -fsS http://localhost:5000/health | jq
docker compose exec worker celery -A worker.celery_app inspect ping
docker compose exec redis redis-cli LLEN celery
docker compose exec worker df -h /mnt/hdd/vault /app/storage /app/logs
docker compose exec worker sh -c 'touch /mnt/hdd/vault/.escrita_ok && rm /mnt/hdd/vault/.escrita_ok && echo OK'
docker system df -v
```

Critérios de aceite:

- `df -h /` → consumo desprezível (~3 GB para SO + código).
- `df -h /mnt/hdd/vault` → cresce conforme imagens e volumes ficam prontos.
- `docker info` → `Docker Root Dir: /mnt/hdd/vault/docker`.
- `curl /health` → 200 com `componentes.servidor/banco/celery = ok`.
- O `touch` final confirma que o container roda como UID 1000 e escreve no sdb1.

---

## Pós-instalação — Webhook do Jira

Configurar a regra de automação do Jira para enviar POST ao endpoint do servidor:

```
URL:     http://<IP-DO-SERVIDOR>:5000/webhook/backup-desligado
Método:  POST
Header:  Content-Type: application/json
Body:    {"descricao": "{{issue.description}}", "ticket_id": "{{issue.key}}"}
```

Detalhes completos em [requisitos-jira-webhook.md](requisitos-jira-webhook.md).

---

## Manutenção recorrente

```bash
# Limpar imagens antigas (após builds repetidos)
docker image prune -f

# Estado dos volumes e uso de disco
docker system df -v
df -h / /mnt/hdd/vault

# Backup do banco SQLite para storage externo
docker run --rm -v automacao-backups_storage_data:/data:ro -v /opt/backups-do-sistema:/dest alpine sh -c 'cp /data/backups.db /dest/backups-$(date +%F).db'

# Antes de mexer no worker, conferir se há backup em curso
curl -s http://localhost:5000/health | jq .backups_em_andamento
```

Sugestão de `cron` (`sudo crontab -e`):

```
0 3 * * 0 docker image prune -f
0 4 * * * docker run --rm -v automacao-backups_storage_data:/data:ro -v /opt/backups-do-sistema:/dest alpine sh -c 'cp /data/backups.db /dest/backups-$(date +\%F).db'
```

---

## Atualizar o código depois de mudanças no repositório

```bash
cd /opt/automacao-backups
git pull
docker compose build && docker compose up -d
docker compose ps
```

> **Antes de `docker compose down`** ou recriar o worker: sempre conferir `curl -s http://localhost:5000/health | jq .backups_em_andamento`. Se houver backup em curso, o worker será interrompido e gerará registro de erro (a rotina de recuperação no startup re-enfileira automaticamente, mas é melhor evitar).

---

## Riscos e mitigações

| Risco | Mitigação |
|---|---|
| sdb1 não monta no boot → Docker cria `/mnt/hdd/vault/docker` no sda | Fase 5 (override systemd) bloqueia start do daemon sem o mount |
| `chown` esquecido em `/mnt/hdd/vault/zips` → falha na primeira compactação | Fase 2 inclui o `chown`; Fase 10 valida com `touch` |
| Daemon inicia antes da configuração do `data-root` | Fase 3 termina com `systemctl stop docker docker.socket` antes da Fase 4 |
| `daemon.json` com JSON inválido → daemon não sobe | `sudo cat` no fim da Fase 4 mostra o conteúdo; `sudo journalctl -u docker -n 50` em caso de falha |
| Logs do daemon Docker enchem disco | `log-opts max-size/max-file` configurados na Fase 4 |
| `/` enche apesar do plano | `df -h /` na Fase 10 prova consumo desprezível; alertar acima de 70% |

---

## Checklist final

- [ ] sdb1 montado em `/mnt/hdd/vault` com `nofail` e `noatime` no fstab
- [ ] `/mnt/hdd/vault/zips` com `chown 1000:1000`
- [ ] `/mnt/hdd/vault/docker` com `chown root:root` e `chmod 711`
- [ ] `/etc/docker/daemon.json` com `data-root` apontando para `/mnt/hdd/vault/docker`
- [ ] Override systemd `Requires=mnt-hdd-vault.mount` aplicado e validado por `systemctl show docker -p Requires`
- [ ] `docker info` mostrando `Docker Root Dir: /mnt/hdd/vault/docker`
- [ ] `service-account.json` em `/opt/automacao-backups/config/credenciais/` com permissão `600`
- [ ] `.env` preenchido com todos os valores reais (chmod 600)
- [ ] Porta 5000 liberada no UFW
- [ ] `docker compose ps` mostrando os 3 containers `(healthy)`
- [ ] `curl http://localhost:5000/health` retorna 200 com componentes `ok`
- [ ] Teste de escrita do container no `/mnt/hdd/vault` passando
- [ ] Webhook do Jira apontando para o servidor e teste manual concluído
