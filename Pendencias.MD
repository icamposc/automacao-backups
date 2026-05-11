# Pendências — Automacao-Backups

Arquivo para registrar tarefas e melhorias que foram identificadas mas não são urgentes.
Cada item deve ter contexto suficiente para retomar a discussão depois.

---

## 1. Avaliar aumento da concorrência do worker Celery

- **Data de registro:** 2026-05-07
- **Status:** Aguardando — não é urgente
- **Decisão atual:** Não alterar agora; aguardar o término do backup em andamento do colaborador David Leandro Leite Ortiz (`david.ortiz@madeiramadeira.com.br`, ticket SPN-61614) antes de qualquer mudança no worker.

### Contexto
- Configuração atual do worker (servidor 10.100.210.200, container `automacao-backups-worker-1`):
  ```
  celery -A worker.celery_app worker --loglevel=info --pool=threads --concurrency=2 \
         --queues=celery --hostname=worker@%h --logfile=/app/logs/celery_worker.log
  ```
- **Limite duro atual:** 2 backups simultâneos. Backups além disso ficam enfileirados no Redis aguardando uma thread liberar.
- Snapshot dos recursos da VM em 2026-05-07 (com 1 backup ativo do David):
  - CPU: 4 vCPU — load average 0,10 (praticamente ocioso)
  - RAM: 7,7 GiB total — 782 MiB em uso, 7,0 GiB livres
  - Disco `/mnt/hdd` (vault): 400 GB — 7,7 GB usados (2%), 393 GB livres
  - Disco `/`: 77 GB — 14 GB usados (19%), 60 GB livres
  - Fila Redis (`celery`): 0 itens; unacked: 0
- Hardware/disco têm folga grande — não são o gargalo. O gargalo é a velocidade do Google Vault e o `--concurrency=2`.

### Pontos de atenção antes de subir a concorrência
- O pool é `threads` (não `prefork`) e `acks_late=True`. A cada reinício do worker, as N tasks ativas voltam à fila e podem ser re-entregues, gerando duplicadas. Hoje o guard "Backup já em andamento" segura isso, mas com mais concorrência o risco de race conditions cresce — revisar antes de aumentar.
- Cada backup, ao final, baixa o ZIP em rajada para `/mnt/hdd` (HDD). Mais backups concorrentes podem saturar I/O do HDD. Já tivemos histórico de incidentes de jbd2 em ext4 (resolvido com migração para XFS em 22/04/2026), mas continua sendo HDD.
- Validar comportamento do `recuperar_backups_interrompidos()` com mais threads no momento do startup.

### Proposta a discutir depois
- Considerar `--concurrency=3` ou `--concurrency=4` após:
  1. Conclusão do backup do David e estabilização da fila.
  2. Revisão do fluxo de re-delivery (`acks_late`) e dos guards de duplicação.
  3. Teste em homologação antes de aplicar em produção (conforme política do CLOUD.md).

---

## 2. Travamentos em Download, Compactação e Monitoramento — plano consolidado

- **Data de registro:** 2026-05-08
- **Status:** Aguardando início — registro de plano completo. Implementação será feita em DEV (WSL local) → homologação → PROD, conforme CLOUD.md.
- **Origem:** análise profunda do código após travamento do backup do David Ortiz (08/05/2026, 6h sem progresso, processo `rm` em D-state durante limpeza, deadlock XFS no `/mnt/hdd`).
- **Branch sugerida:** `fix/download-compactacao-monitoramento`, criada a partir de `fix/vault-monitoramento-resiliente` (preserva v1.1.0 do `utils/retry.py`). Antes do merge em `master`, limpar o histórico para remover a senha versionada (ver pendência 3).

### 2.1 Achados críticos da análise (referência rápida)

| ID | Arquivo / Linha | Problema | Severidade |
|---|---|---|---|
| P1.1 | gap geral | Nenhum pré-cheque de capacidade vs `stats.sizeInBytes` retornado pelo Vault na Etapa 3 | 🔴 |
| P1.2 | `servicos/vault_exportacao.py:471` (`source.read(...)`) | Read sem timeout efetivo no GCS — causa raiz dos travamentos de horas | 🔴 |
| P1.3 | `servicos/vault_exportacao.py` (geral) | Sem watchdog: 0 byte/s não dispara nenhum alerta nem retry | 🟠 |
| P1.4 | `servicos/vault_exportacao.py:512` | `ThreadPoolExecutor(max_workers=6)` em HDD rotacional gera contenção (`%util=100%` com `r/s=w/s=0`) | 🟠 |
| P2.1 | `processamento/orquestrador.py:166` | ZIP gerado dentro de `pasta_colaborador` — dobra pico de uso de disco | 🔴 |
| P2.2 | `processamento/compactacao.py:84` | `ZIP_DEFLATED` em `.pst` e `.zip` (já comprimidos) — CPU 100% por horas com ganho próximo a 0% | 🔴 |
| P2.3 | `processamento/compactacao.py:106-107` | SHA256 em segundo passe (re-leitura completa do ZIP) | 🟠 |
| P2.4 | `processamento/compactacao.py` | Sem callback de progresso na compactação — Etapa 5 fica "muda" no DB | 🟠 |
| P3.1 | `docker-compose.yml` (raiz) | Sem healthcheck no servidor e worker (o `deploy/` tem, mas não é o efetivo em prod) | 🔴 |
| P3.2 | gap geral | Sem detector de "backup parado": `status='em_andamento' AND inicio < now() - 12h` nunca é checado | 🔴 |
| P3.3 | `processamento/recuperacao.py` | Recovery cego: re-enfileira sem checar histórico de falhas nem capacidade — risco de loop infinito | 🔴 |
| N1 | `app/servidor.py:48` | `recuperar_backups_interrompidos()` chamado na carga do módulo. Drift: prod hoje em `--workers 1`, mas compose configura `--workers 2` (bug double-recovery volta a ser latente) | 🟠 |
| N2 | `app/servidor.py:115-122` | `/saude` retorna `200 OK` sempre, sem checar nada — mascara falhas se algum monitor externo apontar para ele | 🟠 |
| N3 | `app/servidor.py:125-183` | `/health` superficial: só checa banco e Redis; não detecta worker em D-state nem disco cheio | 🔴 |
| N4 | `servicos/drive_upload.py:131` | Mesmo bug de timeout do download: `sessao.put(url_upload, ..., data=chunk)` sem `timeout=` | 🔴 |
| N5 | `servicos/drive_upload.py:104` | Retry "resumível" descarta a sessão e recomeça do zero — para 800 GB, 3 tentativas = 2,4 TB de upload | 🔴 |
| N6 | `servicos/vault_exportacao.py` (`_on_chunk`) | `atualizar_progresso` faz `UPDATE` no SQLite por chunk de 10 MB (~180k UPDATEs em backup de 1,7 TB) | 🟡 |
| N7 | `processamento/recuperacao.py` | Mesmo após múltiplas falhas, recovery não tem blacklist por ticket | 🟠 |
| N8 | `utils/logger.py` | Sem `RotatingFileHandler` — `LOGS_TAMANHO_MAXIMO_GB` existe, mas só é consumido por limpeza periódica | 🟡 |
| N10 | `servicos/vault_exportacao.py` (`baixar_exportacao`) | `chamar_com_retry_rede` (v1.1.0 do retry resiliente) não foi replicada no download — `obter_cliente_storage()` é compartilhado entre threads por horas | 🟠 |

### 2.2 Plano consolidado em fases

#### Fase 1 — Quick wins (sem mudança de arquitetura)
PR sugerido: 3 PRs pequenos para revisões focadas (CLOUD.md "não abra PRs gigantes").

**PR 1 — Download e compactação (ataca direto o sintoma do David)**
- 1.1 `servicos/vault_exportacao.py`: reduzir `ThreadPoolExecutor(max_workers=6)` → `2`
- 1.2 `servicos/vault_exportacao.py`: substituir `blob.open(...).read()` por `blob.download_to_filename(..., timeout=(60, 600), checksum=None)` ou wrapper com watchdog
- 1.3 `servicos/drive_upload.py`: adicionar `timeout=(connect, read)` em `sessao.put()` e `sessao.post()`
- 1.4 `processamento/compactacao.py`: trocar `ZIP_DEFLATED` por `ZIP_STORED`, adicionar `allowZip64=True`, expor callback `on_progresso(pct)`
- 1.5 `processamento/orquestrador.py`: ZIP fora de `pasta_colaborador` — usar `/mnt/hdd/zips/{email}_{ts}.zip` (subpasta irmã, não filha)
- 1.12 `vault_exportacao.py` + `drive_upload.py`: throttle no progresso (max 1% ou 30s, o que vier antes — não por chunk)
- 1.13 `drive_upload.py`: `logger.info` por chunk → `logger.debug` ou eliminar
- Atualizar `testes/test_compactacao.py` (corrigir desempacotamento da tupla retornada) e adicionar teste de `ZIP_STORED` e callback

**PR 2 — Pre-flight de capacidade e contenção do recovery cego**
- 1.6 `processamento/orquestrador.py` (Etapa 3 → 4): ler `stats.sizeInBytes` dos dois exports, comparar com `shutil.disk_usage(PASTA_VAULT).free * 0.8`; se não cabe → `ErroEspacoInsuficiente`, alerta no Chat
- 1.7 `processamento/recuperacao.py`: consultar `count(*) where ticket_id=? AND status_geral='erro'`. Se ≥ 3, NÃO re-enfileirar; alerta. Adicionar blacklist por ticket após N falhas
- N7 (relacionado): mesma mudança de blacklist cobre o gap de loop pós-erro
- Adicionar testes unitários (mock de `disk_usage` e `stats`)

**PR 3 — Saúde e monitoramento**
- 1.8 `app/servidor.py`: remover `/saude` ou fazer chamar internamente o `/health`
- 1.9 `app/servidor.py`: enriquecer `/health` com:
  - `celery -A worker.celery_app inspect ping --timeout 5`
  - `shutil.disk_usage(PASTA_VAULT)` — alerta abaixo de 20%
  - "backups travados há > 12h" (query no DB)
- 1.10 `docker-compose.yml` (raiz): adicionar healthcheck no servidor (`/health`) e worker (`celery inspect ping`)
- 1.11 `utils/logger.py`: `RotatingFileHandler(maxBytes=100MB, backupCount=10)`
- Adicionar teste de integração no endpoint `/health`

#### Fase 2 — Robustez (após Fase 1 estabilizar)
- 2.1 Watchdog de progresso no download — thread auxiliar mata o read se `bytes_baixados` não cresce em 10 min
- 2.2 Aplicar `chamar_com_retry_rede` ao GCS no download (`baixar_exportacao`) e ao Drive no upload
- 2.3 Job periódico (Celery beat) que marca `em_andamento > 12h` como erro e alerta no Chat (novo `processamento/watchdog.py`)
- 2.4 Migrar `recuperar_backups_interrompidos` da carga do módulo para tarefa Celery one-shot na inicialização (idempotente entre workers — corrige N1 estruturalmente)
- 2.5 Avaliar `acks_late=False` ou migrar para `pool=prefork` (se manter 18 exports simultâneos, precisa de Redis-based semaphore)

#### Fase 3 — Rearquitetura (opcional)
- 3.1 Streaming GCS → Drive sem ZIP intermediário em disco para backups acima de um limite (ex.: > 200 GB) — elimina o gargalo do HDD e o limite de capacidade
- 3.2 Métricas Prometheus: node_exporter no host + cAdvisor + alerta `iowait > 50% por 10 min`
- 3.3 Avaliação do scheduler I/O do HDD na VM Proxmox (`mq-deadline` vs `none`); ajuste de queue depth virtio

### 2.3 Estratégia DEV → PROD (definida em 08/05/2026)

- DEV / Homologação: WSL local em `/home/ivancampos/Projetos/ProjetosITO/automacao-backups`
- PROD: VM `10.100.210.200`
- Fluxo: branch `fix/<tema>` → `python -m pytest` localmente → `docker compose up -d --build` no WSL para validação manual → PR → revisão → merge em `master` → deploy manual em prod (`git pull && docker compose build && docker compose up -d`) → validação pós-deploy
- Detalhes em `docs/AMBIENTES.md` (untracked, não versionado ainda)

### 2.4 Pré-flight necessário antes de qualquer deploy em PROD

- Servidor `10.100.210.200` está em I/O deadlock no `/mnt/hdd` (XFS). `qm reset 100` no host Proxmox 10.100.210.211 ainda não foi autorizado.
- Resíduo do David em `/mnt/hdd/vault/david.ortiz@..._20260507_144158/` precisa ser apagado (parcial, ~600 MB no momento do travamento).
- Decidir destino do backup do David: 1,7 TB no Vault não cabe no pool atual (400 GB). Sem expansão de disco ou particionamento, o backup não é viável no fluxo atual.

---

## 3. Senha SSH versionada em `docs/CONTEXTO-OPERACIONAL.md`

- **Data de registro:** 2026-05-08
- **Status:** Edição local feita (working tree do WSL), **sem commit** por orientação. Senha continua presente no histórico do Git remoto das branches `fix/*`.
- **Risco:** viola CLOUD.md ("Não publique chaves de API, tokens de acesso, secrets ou senhas em repositórios"). Repo é privado (`github.com/icamposc/automacao-backups`), mas a regra aplica independentemente da visibilidade.

### Onde a senha aparece (commit `297754a`)

| Local | Tem senha? |
|---|---|
| `master` (local + `origin/master`) | não |
| `fix/vault-monitoramento-resiliente` (local + `origin/...`) | sim — `docs/CONTEXTO-OPERACIONAL.md:37` no histórico |
| `fix/vault-search-method` (local + `origin/...`) | sim — mesmo arquivo, mesma linha |

### Edições locais já aplicadas (não commitadas)

- `docs/CONTEXTO-OPERACIONAL.md:34-48`: linha com a senha removida; substituída por instrução de buscar credencial em `~/.credentials` (prioridade 1), `.env` na raiz do projeto (prioridade 2) ou solicitar ao responsável de infra (prioridade 3).
- `docs/CONTEXTO-OPERACIONAL.md:218-224`: exemplo de SSH com `/tmp/askpass.sh` substituído por `sshpass -e` lendo `SSHPASS` extraído de `~/.credentials` via `awk` (sem expor senha em logs).
- `docs/AMBIENTES.md` (novo, untracked): documenta ambientes, acesso SSH, fluxo DEV → PROD — sem senhas.

### Plano para encerrar a pendência

1. **Rotacionar a senha SSH** dos servidores `10.100.210.200` e `10.100.210.211` — necessário independentemente da limpeza do histórico (já foi vista por quem clonou).
2. Atualizar `~/.credentials` local com a nova senha.
3. Decidir entre dois caminhos para o histórico Git:
   - **Caminho A — descartar branches `fix/*`:** se o trabalho dessas branches já foi mergeado/superado, deletar `origin/fix/vault-monitoramento-resiliente` e `origin/fix/vault-search-method`. Mais simples.
   - **Caminho B — limpar histórico:** `git filter-repo --replace-text` (ou BFG) para remover a senha de todos os commits (string ficou registrada apenas em `~/.credentials`, não neste arquivo), depois `git push --force` em ambas as branches. Requer coordenação com qualquer outro clone existente.
4. Commitar as edições atuais de `docs/CONTEXTO-OPERACIONAL.md` em uma branch a partir de `master` (não a partir de `fix/*`) para que o `master` finalmente tenha a versão limpa do doc.
5. Mover `docs/AMBIENTES.md` para a mesma branch e abrir PR de "documentação de ambientes e acesso".

---

## 4. Drift entre `deploy/docker-compose.yml` (repo) e o que roda em PROD

- **Data de registro:** 2026-05-08
- **Status:** Registrado, sem ação tomada.

### Discrepâncias

| Item | Repo `deploy/docker-compose.yml` | PROD efetivo (`/opt/automacao-backups/docker-compose.yml`) |
|---|---|---|
| Worker concurrency | `--concurrency=10` | `--concurrency=2` |
| Servidor workers | `--workers 2` | `--workers 1` |
| Healthcheck servidor | sim (`/health`) | não |
| Healthcheck worker | sim (`celery inspect ping`) | não |
| Logging json-file | configurado | não |
| Container_name | sim | não |
| Image | `automacao-backups:latest` | `build: .` |

O compose efetivamente em uso em prod é o `docker-compose.yml` da raiz, não o de `deploy/`. Isso explica por que prod não tem healthchecks e roda com concurrency menor.

### Próximo passo

- Antes de qualquer mudança estrutural no compose, alinhar a divergência: decidir qual é o compose canônico para PROD. Sugestão: usar o `deploy/docker-compose.yml` como fonte da verdade (já tem healthchecks, logging, container_name) e ajustar concurrency conforme a pendência 1 deste arquivo.

---

## 5. Incidente em aberto — Servidor 10.100.210.200 em I/O deadlock + David Ortiz incompleto

- **Data de registro:** 2026-05-08
- **Status:** Aguardando autorização para `qm reset 100` no host Proxmox 10.100.210.211.
- **Sintomas:** processo `rm -rfv` em D-state, `__inode_wait_for_writeback`, `%util=100%` com `r/s=w/s=0` no `/dev/sdb`. Mesmo padrão dos incidentes 20/04 (ext4 jbd2) e 22/04 (XFS pós-migração) — causa raiz aparente abaixo do filesystem (HDD físico, virtio, controlador).

### Estado do backup do David Ortiz (`SPN-61614`)

- 4 tentativas registradas no DB; todas como `erro` (id=4 marcado manualmente em 2026-05-08T19:21:01 UTC).
- Vault no Google: Drive 1.668,68 GB + Email 97,66 GB = ~1,77 TB.
- Pool `/mnt/hdd`: 400 GB total. **Não cabe.**
- Vault parcial em disco no momento do travamento: ~603 MB residuais em `/mnt/hdd/vault/david.ortiz@madeiramadeira.com.br_20260507_144158/`.

### Ações pendentes

1. Hard reset da VM (`qm reset 100`) — aguardando autorização.
2. Após reset: apagar resíduo `/mnt/hdd/vault/david.ortiz@..._20260507_144158/`.
3. Decisão sobre o David: expandir disco, particionar download por subpasta no Vault, ou abandonar.
4. Investigação da causa raiz do deadlock no host Proxmox: `dmesg` (`task X blocked for more than 120 seconds`), SMART do HDD, scheduler I/O da VM (`mq-deadline` vs `none`), queue depth virtio.
