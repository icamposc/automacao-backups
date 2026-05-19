# Análise de TCO — Infraestrutura do Sistema de Automação de Backups

**Documento de referência para construção de paper de defesa de investimento**

| Campo | Valor |
|---|---|
| Versão | 1.1 (rascunho) |
| Data | 13/05/2026 |
| Autor | Ivan Campos / Plataformas |
| Empresa | MadeiraMadeira — Direção de Infraestrutura |
| Status | Em construção — itens marcados *(a confirmar)* requerem validação antes da submissão |

---

## 1. Objetivo

Documentar, comparar e justificar economicamente o investimento em infraestrutura para sustentar o Sistema de Automação de Backups (backup de Gmail + Google Drive de colaboradores desligados via Google Vault API), em regime 24/7, com requisito de capacidade útil de 6–8 TB.

Cinco cenários são analisados e comparados em horizonte de 1, 3 e 5 anos:

- **Cenário A** — Status quo (Dell PowerEdge R430 atual, sem mudanças)
- **Cenário B** — Upgrade do R430 atual (SSD enterprise SATA, mantendo controladora PERC H330)
- **Cenário C** — Aquisição de servidor novo Dell PowerEdge R660xs com NVMe PCIe 4.0
- **Cenário D** — Migração para Google Cloud Platform na região `us-east1`
- **Cenário E** — Migração para Google Cloud Platform na região `southamerica-east1` (São Paulo)

Além da análise comparativa de custos de infraestrutura, o paper quantifica a **economia de hora-homem (HH)** decorrente da automação do processo de backup, que substitui um fluxo manual previamente executado pelo time de Plataformas.

---

## 2. Contexto operacional

### 2.1 Sistema atual

O Sistema de Automação de Backups é uma aplicação Python (Flask + Celery + Redis + SQLite) que recebe notificações de desligamento de colaboradores via webhook, dispara exportações de Gmail e Drive pelo Google Vault, baixa os arquivos a partir do Google Cloud Storage, gera arquivos compactados com hash SHA-256, armazena temporariamente em vault local e move os pacotes para storage definitivo, podendo opcionalmente desativar a conta de origem.

A arquitetura é composta por:

- **Servidor Flask** (Gunicorn `--workers 1`) — expõe o webhook em porta 5000
- **Worker Celery** (`--pool threads --concurrency 2`) — executa as tarefas de backup
- **Redis** — broker e backend de resultados Celery
- **SQLite** em `/app/storage/backups.db` — persistência de estado de cada backup
- **Vault local** em `/mnt/hdd/vault/` (XFS) — armazenamento temporário de exports do Google Vault

### 2.2 Hospedagem atual

O sistema roda como VM em hipervisor Proxmox (host físico 10.100.210.211), em servidor **Dell PowerEdge R430** (geração Dell PowerEdge 13G, lançamento 2015).

### 2.3 Inventário de hardware do R430 (evidência `lshw`)

```text
/0/400                           processor      Intel(R) Xeon(R) CPU E5-2698 v4 @ 2.20GHz
/0/401                           processor      CPU [empty]
/0/1000                          memory         32GiB System Memory
/0/1000/0                        memory         16GiB DIMM DDR4 Registered 2400 MHz
/0/1000/1                        memory         16GiB DIMM DDR4 Registered 2400 MHz
/0/1000/2..b                     memory         [empty]                       (10 slots livres)
/0/100/1/0           scsi0       storage        MegaRAID SAS-3 3008 [Fury]
/0/100/1/0/2.0.0     /dev/sda    disk           1799GB PERC H330 Mini         (RAID 1, 1,8 TB úteis)
```

| Componente | Especificação atual | Capacidade máxima |
|---|---|---|
| CPU socket 1 | 1× Xeon E5-2698 v4 (20C/40T, 2,2 GHz, 50 MB L3, 135 W) | — |
| CPU socket 2 | **vazio** | 1× CPU adicional (até E5-2699 v4) |
| RAM | 32 GB DDR4 ECC RDIMM 2400 (2× 16 GB em 2 de 12 slots) | 384 GB (12× 32 GB) com 1 CPU; 1,5 TB com 2 CPUs |
| Storage | 2× SAS ~1,8 TB em RAID 1 (1,8 TB úteis, "bem usado") | 4× 3,5" LFF *ou* 8× 2,5" SFF |
| Controladora RAID | Dell PERC H330 Mini (Broadcom/LSI SAS3008, PCIe 3.0 x8) | Suporta SAS3 12 Gb/s + SATA 6 Gb/s, RAID 0/1/5/10/50, sem cache, sem NVMe |
| PCIe | 2–3 slots PCIe 3.0 | — |

### 2.4 Diagnóstico técnico

- O ativo está em **boa saúde operacional**, sem incidentes recorrentes de hardware reportados.
- Os 1,8 TB úteis atuais são **insuficientes** para o requisito de 6–8 TB do vault consolidado.
- A controladora PERC H330 **não suporta NVMe** nativamente, mas suporta SSD SAS/SATA enterprise.
- A RAM atual (32 GB em 2 slots) deixa **10 slots vazios**; só 2 dos 4 canais de memória estão ativos, comprometendo ~50% da largura de banda nominal.
- A CPU E5-2698 v4 entra em fim de suporte pela Intel; chassi R430 está em fim de ProSupport Dell entre 2025–2027.

### 2.5 Histórico de incidentes relevantes

| Data | Incidente | Resolução |
|---|---|---|
| 20/04/2026 | Deadlock `jbd2/sdb-8` no journal ext4 do HDD do vault (D-state por 2d18h) | Reboot + migração ext4 → XFS |
| 22/04/2026 | Reincidência de I/O wait extremo (jbd2) | Hard reset via Proxmox (`qm reset 100`); causa raiz pendente |
| Histórico | Storm de tasks Celery duplicadas (acks_late + multi-worker Gunicorn) | Gunicorn reduzido a `--workers 1`; bug residual de re-delivery permanece |

Esses incidentes reforçam o gargalo de **I/O** e a necessidade de upgrade do subsistema de storage.

---

## 3. Premissas metodológicas

| Premissa | Valor adotado |
|---|---|
| Regime de operação | 24/7 (730 horas/mês, 8.760 horas/ano) |
| Workload alvo compute | 8 vCPU equivalentes / 32 GB RAM |
| Capacidade alvo storage | 6 TB a 8 TB úteis |
| Cotação cambial | USD 1 = **BRL 4,89** (PTAX Banco Central, 11/05/2026) |
| Escopo on-premise | Apenas **CAPEX** de hardware (sem energia, datacenter, mão de obra) |
| Escopo cloud GCP | Full-cost compute + storage primário; egress excluído |
| Regiões GCP | `southamerica-east1` (São Paulo) e `us-east1` (Carolina do Sul) |
| Modelos GCP | On-demand, CUD 1 ano (−37%), CUD 3 anos (−55%) |
| Horizontes analisados | 1 ano, 3 anos, 5 anos |

**Assimetria reconhecida:** o cenário on-premise CAPEX-only não embute custos operacionais que a cloud já inclui (energia, refrigeração, redundância de site, manutenção, SLA). Esta assimetria é intencional nesta versão; será endereçada com cenário de TCO completo on-premise antes da submissão final.

---

## 4. Cenário A — Status quo (R430 sem alterações)

### 4.1 Configuração

Hardware atual, sem investimento adicional. Mantém:

- 1× Xeon E5-2698 v4
- 32 GB DDR4
- 2× SAS 1,8 TB em RAID 1 (1,8 TB úteis)
- PERC H330 Mini

### 4.2 Custo

| Item | Valor BRL |
|---|---:|
| CAPEX adicional | **R$ 0** |
| Risco residual | Hardware fim de vida, capacidade insuficiente |

### 4.3 Avaliação

**Não atende ao requisito de capacidade** (1,8 TB úteis vs 6–8 TB necessários). Apresentado apenas como baseline de custo zero. Não constitui solução viável para a workload projetada.

---

## 5. Cenário B — Upgrade do R430 atual (SSD enterprise SATA)

### 5.1 Justificativa técnica

A controladora PERC H330 suporta SSD SAS/SATA enterprise nativamente. O workload de backup é predominantemente sequencial (download Vault → ZIP → SHA-256 → upload Drive), com baixa demanda de IOPS aleatória. SSD SATA enterprise com 1 DWPD entrega:

- ~550 MB/s sequencial sustentado (vs ~250 MB/s do HDD SAS atual)
- ~0,1 ms de latência (vs ~5 ms do HDD)
- Endurance de 7,68 TB de escrita/dia para drive de 7,68 TB (muito acima do uso real)
- Rebuild RAID em ~1–2 h (vs ~24 h em HDD)

PCIe 4.0 NVMe entregaria ~7 GB/s teóricos, mas o gargalo real do workload está na rede (1 GbE = 100 MB/s sustentado) e no throughput da Google Vault API — torna o SATA enterprise suficiente em termos práticos.

### 5.2 Configuração proposta

| Componente | Especificação |
|---|---|
| Servidor base | Dell PowerEdge R430 (reaproveitado, CAPEX zero) |
| CPU | 1× Xeon E5-2698 v4 (mantida) |
| RAM | 32 GB DDR4 atual + (opcional) 32 GB adicionais |
| Storage | 4× Kingston DC600M 3,84 TB SATA enterprise em RAID 10 |
| Controladora | PERC H330 Mini (mantida) |
| Capacidade útil | 7,68 TB com tolerância a 2 falhas (uma por par espelhado) |

### 5.3 Composição de custos

| Item | Valor BRL |
|---|---:|
| 4× Kingston DC600M 3,84 TB SATA enterprise | 16.000 |
| (Opcional) 2× DDR4 16 GB ECC RDIMM 2400 — completa 64 GB | 800 |
| **CAPEX total (mínimo)** | **R$ 16.000** |
| **CAPEX total (com upgrade RAM para 64 GB)** | **R$ 16.800** |

### 5.4 Limitações reconhecidas

- **PCIe 3.0 + SATA 6 Gb/s:** throughput limitado a ~550 MB/s por drive (vs ~7 GB/s NVMe PCIe 4.0)
- **Chassi geração 2015 (Gen13):** fim de ProSupport Dell entre 2025–2027 — risco de falta de peças após esse prazo
- **Sem AVX-512, sem DDR5:** irrelevante para o workload de backup
- **PERC H330 não suporta RAID 6** — dupla tolerância só via RAID 10

### 5.5 Amortização

| Horizonte | R$/ano amortizado |
|---|---:|
| 1 ano | 16.000 |
| 3 anos | 5.333 |
| 5 anos | 3.200 |

---

## 6. Cenário C — Servidor novo Dell PowerEdge R660xs com NVMe PCIe 4.0

### 6.1 Configuração proposta

| Componente | Especificação |
|---|---|
| Servidor | Dell PowerEdge R660xs 1U (Gen16, 2023) |
| CPU | 1× Intel Xeon Silver 4410Y (12C/24T, Sapphire Rapids, 2,0 GHz, 150 W) |
| RAM | 32 GB DDR5 ECC RDIMM 4800 MHz (expansível a 4 TB) |
| Storage | 2× Samsung PM9A3 3,84 TB U.2 PCIe 4.0 (1 DWPD) — JBOD ou RAID 1 |
| Controladora | NVMe direto via backplane PCIe 4.0 |
| Garantia | 3 anos on-site Dell ProSupport incluída |

### 6.2 Composição de custos

| Item | Valor BRL |
|---|---:|
| Chassi + CPU + 32 GB + fontes redundantes | 33.500 |
| 2× SSD NVMe PM9A3 3,84 TB | 8.000 |
| **CAPEX total (7,68 TB úteis JBOD)** | **R$ 41.500** |

Variação com Dell ProSupport 5 anos: +R$ 3.000 a 6.000 — **a confirmar via cotação Dell Brasil**.

### 6.3 Ganhos técnicos vs Cenário A (R430 atual)

| Métrica | R430 atual | R660xs novo | Ganho |
|---|---|---|---:|
| Capacidade útil | 1,8 TB | 7,68 TB | +327% |
| Throughput storage | SAS 12 Gb/s (~250 MB/s) | NVMe PCIe 4.0 (~7 GB/s) | ~28× |
| RAM | DDR4-2400 (~76 GB/s banda) | DDR5-4800 (~230 GB/s banda) | ~3× |
| Geração CPU | Broadwell-EP (2016) | Sapphire Rapids (2023) | +37% IPC |
| Suporte Dell | Até 2025–2027 | Até 2028–2030 | +3 anos |

### 6.4 Alternativa de CPU (mais cores)

Se houver intenção de consolidar mais workloads no chassi, substituir o Silver 4410Y (12C/24T) por **Xeon Silver 4416Y (16C/32T)** adiciona ~R$ 2.000–3.000 ao CAPEX (total R$ 43,5k–44,5k).

### 6.5 Amortização

| Horizonte | R$/ano amortizado |
|---|---:|
| 1 ano | 41.500 |
| 3 anos | 13.833 |
| 5 anos | 8.300 |

---

## 7. Cenário D — GCP `us-east1`

### 7.1 Configuração equivalente

| Componente | SKU GCP |
|---|---|
| Compute | `n2-standard-8` (8 vCPU / 32 GB RAM) |
| Storage primário | `pd-ssd` 6 TB ou 8 TB |
| Região | `us-east1` (Moncks Corner, Carolina do Sul) |

**Nota técnica:** `pd-ssd` é tecnologicamente equivalente a NVMe gen3/datacenter. Equivalente real a PCIe 4.0 seria **Hyperdisk Extreme**, com cobrança adicional por IOPS provisionada (~US$ 700/mês extras para equiparar throughput PCIe 4.0). Preços de Hyperdisk em SP **a confirmar** em <https://cloud.google.com/products/calculator>.

### 7.2 Preços compute `n2-standard-8` (24/7)

| Modelo | USD/hora | USD/mês | USD/ano |
|---|---:|---:|---:|
| On-demand | 0,3885 | 283,58 | 3.403,02 |
| CUD 1 ano (−37%) | 0,2448 | 178,66 | 2.143,90 |
| CUD 3 anos (−55%) | 0,1748 | 127,61 | 1.531,36 |

### 7.3 Preços storage `pd-ssd`

| Capacidade | USD/GiB-mês | USD/mês | USD/ano |
|---|---:|---:|---:|
| 6 TB (6.144 GiB) | 0,170 | 1.044,48 | 12.533,76 |
| 8 TB (8.192 GiB) | 0,170 | 1.392,64 | 16.711,68 |

CUD não se aplica a Persistent Disk.

### 7.4 TCO consolidado (compute + 6 TB pd-ssd)

| Modelo | 1 ano (R$) | 3 anos (R$) | 5 anos (R$) |
|---|---:|---:|---:|
| On-demand | 77.932 | 233.793 | 389.654 |
| CUD 1 ano | 71.745 | 215.235 | 358.725 |
| **CUD 3 anos** | **68.778** | **206.334** | **343.890** |

---

## 8. Cenário E — GCP `southamerica-east1` (São Paulo)

### 8.1 Diferencial técnico

A região São Paulo entrega menor latência para usuários no Brasil, ao custo de **~55% acima de `us-east1`** para compute N2 e ~30% acima para storage SSD.

### 8.2 Preços compute `n2-standard-8` em SP

| Modelo | USD/hora | USD/mês | USD/ano |
|---|---:|---:|---:|
| On-demand | 0,6019 | 439,39 | 5.272,73 |
| CUD 1 ano (−37%) | 0,3792 | 276,82 | 3.321,82 |
| CUD 3 anos (−55%) | 0,2709 | 197,73 | 2.372,73 |

Cálculo on-demand SP: 8 vCPU × US$ 0,046/h + 32 GB × US$ 0,0061675/h. *(Valor exato a confirmar em calculator.cloud.google.com)*

### 8.3 Preços storage `pd-ssd` em SP

| Capacidade | USD/GiB-mês *(estimado, ~1,3× us-east1)* | USD/mês |
|---|---:|---:|
| 6 TB | ~0,221 | ~1.357,82 |
| 8 TB | ~0,221 | ~1.810,43 |

### 8.4 TCO consolidado (compute + 6 TB pd-ssd) em SP

| Modelo | 1 ano (R$) | 3 anos (R$) | 5 anos (R$) |
|---|---:|---:|---:|
| On-demand | ~105.461 | ~316.383 | ~527.305 |
| CUD 1 ano | ~95.480 | ~286.440 | ~477.400 |
| **CUD 3 anos** | **~91.280** | **~273.839** | **~456.398** |

---

## 9. Análise de capacidade computacional e escalabilidade

Considerando-se que o R430 atual e o R660xs novo possuem RAM e CPU suficientes para o workload, esta seção avalia a margem de escalabilidade do número de workers Celery em cada cenário on-premise.

### 9.1 Bottlenecks por ordem de saturação

1. Storage I/O (gargalo principal hoje no R430 com HDD SAS — eliminado nos Cenários B e C)
2. Largura de banda de rede (1 GbE = ~80–100 MB/s sustentado real)
3. Quota da Google Vault API (limites por projeto OAuth)
4. RAM por task (~1–2 GB pico em download + ZIP + SHA-256)
5. CPU (último a saturar — workload é I/O bound)

### 9.2 Workers viáveis vs RAM disponível

| RAM | CAPEX RAM extra | Workers Celery viáveis | Gargalo provável |
|---|---:|---:|---|
| 32 GB (atual) | 0 | ~10–12 | RAM + storage |
| 64 GB | ~R$ 800 (2× 16 GB DDR4) | ~25–30 | Rede + storage |
| 128 GB | ~R$ 2.400 (6× 16 GB DDR4) | ~50–60 | Rede + quota API |
| 256 GB (com 2 CPUs) | ~R$ 4.000 | ~80–100 | Quota API |

Cálculo: cada task ativa consome ~1,5 GB de RAM (estimado); 4 GB reservados para SO + Redis + Postgres + Flask; folga operacional de 20%.

### 9.3 Recomendações pragmáticas

- **Sem custo:** elevar `--concurrency` de 2 para 6–8 no setup atual deve aumentar throughput 3–4× sem trocar hardware. Validar com monitoramento de RAM e fila Redis.
- **Com R$ 800 (RAM 64 GB):** atinge confortavelmente `--concurrency=20`. Sempre instalar DIMMs em conjuntos de 4 para preencher os 4 canais de memória da CPU 1.
- **Acima de R$ 4.000:** a quota da Google Vault API ou a rede 1 GbE virão a saturar antes do hardware — esforço com retorno decrescente.

### 9.4 Alterações de software complementares

- Migrar Celery de `--pool=threads` para `--pool=prefork` quando ultrapassar `--concurrency=15` (threads pool sofre com GIL acima desse patamar para tasks I/O + CPU mistas como SHA-256).
- Avaliar troca de SQLite por PostgreSQL caso o volume de registros em `backups.db` ultrapasse ~100k linhas; SQLite tem locking global e pode virar gargalo em alta concorrência.
- Solicitar aumento de quota Google Vault API via console GCP antes de escalar para > 20 workers concorrentes.

---

## 10. Análise econômica do tempo operacional liberado (hora-homem)

### 10.1 Justificativa

Antes da automação, as tarefas de backup de colaboradores desligados eram executadas manualmente, com supervisão e intervenção do time de Plataformas em cada etapa do fluxo (configuração de export no Vault, monitoramento do GCS, download dos artefatos, validação de integridade, organização, arquivamento e encerramento da conta de origem).

O Sistema de Automação de Backups elimina essa carga operacional, liberando tempo do profissional sênior responsável para atividades de maior valor agregado (engenharia de plataforma, projetos de melhoria, suporte a outras automações). Esta seção quantifica esse tempo liberado em moeda corrente, agregando ao TCO uma dimensão de **economia de hora-homem (HH)** que complementa a análise de custos de infraestrutura.

### 10.2 Premissa de tempo liberado

Conforme registro do responsável atual pelo processo, o tempo médio gasto em tarefas manuais de backup é de **17 horas/mês**, distribuídas entre:

- Configuração e disparo de exports no Google Vault
- Monitoramento e acompanhamento da geração dos exports
- Download manual dos artefatos a partir do Google Cloud Storage
- Validação de integridade (cálculo e conferência de SHA-256)
- Organização, compactação e arquivamento em destino final
- Atualização de tickets de SPN e comunicação com stakeholders
- Encerramento e auditoria das contas de origem

Total anualizado: **204 horas/ano** (17 h × 12 meses).

### 10.3 Metodologia do cálculo de hora-homem

Para o cálculo do custo total da hora-homem ao empregador adota-se a fórmula:

```
Custo HH = (Salário base mensal × Multiplicador de encargos) / Horas úteis mensais
```

**Premissas adotadas:**

| Parâmetro | Valor |
|---|---|
| Perfil de referência | Engenheiro de Plataforma Sênior — vínculo CLT |
| Multiplicador de encargos | **1,9×** sobre o salário bruto |
| Composição do multiplicador | INSS patronal (20%) + FGTS (8%) + provisão de férias + 1/3 + 13° salário + rescisão + plano de saúde + VR/VA + VT + seguro de vida |
| Horas úteis mensais | **176 h** (22 dias × 8 h) |
| Base salarial | Estrutura interna MadeiraMadeira para o perfil (valor nominal não detalhado neste documento) |
| **Custo hora-homem adotado** | **R$ 162/h** |

O valor de R$ 162/h é compatível com a faixa pública de mercado para Engenheiro de Plataforma Sênior em São Paulo (Glassdoor BR, Catho, Salário.com.br — maio/2026), o que mantém o cálculo defensável mesmo sob escrutínio externo.

### 10.4 Economia anual e em horizontes

| Horizonte | Horas liberadas | Economia HH (R$) |
|---|---:|---:|
| 1 ano | 204 h | **33.048** |
| 3 anos | 612 h | **99.144** |
| 5 anos | 1.020 h | **165.240** |

### 10.5 ROI consolidado do Cenário B (CAPEX + economia de HH)

Considerando o investimento recomendado (Cenário B — R$ 16.800 CAPEX) e o tempo operacional liberado:

| Métrica | Valor |
|---|---:|
| CAPEX Cenário B | R$ 16.800 |
| Economia HH em 5 anos | R$ 165.240 |
| **Retorno líquido em 5 anos (economia − CAPEX)** | **R$ 148.440** |
| **Payback considerando apenas economia de HH** | **6,1 meses** |
| Payback combinado (HH + custo evitado de migração à GCP SP) | **< 1 mês** |

### 10.6 Benefícios qualitativos adicionais (não monetizados)

Os ganhos descritos abaixo são relevantes para a defesa do investimento mas não foram convertidos em moeda corrente neste documento:

- **Redução de risco operacional:** o processo manual está sujeito a erro humano (export incompleto, download corrompido, conta encerrada antes do término do backup, esquecimento de etapas). A automação aplica validação SHA-256 sistemática e mantém histórico estruturado e auditável em SQLite.
- **Continuidade de processo:** a execução manual depende do profissional disponível. O sistema automatizado executa 24/7 independentemente de férias, afastamentos, picos de demanda ou turnover.
- **Escalabilidade linear vs constante:** o tempo manual cresce linearmente com o número de desligamentos por mês. A automação escala por meio de paralelismo de workers Celery sem custo adicional de hora-homem.
- **Conformidade e auditoria:** registros estruturados no banco e tickets SPN viabilizam atendimento a auditorias internas e externas (LGPD, ISO 27001) com baixíssimo custo de extração de evidências.
- **Padronização e qualidade:** garantia de que todos os backups seguem o mesmo procedimento, com os mesmos parâmetros, sem variação inter-operadores.

---

## 11. Tabela comparativa consolidada

### 11.1 TCO total em 5 anos — infraestrutura (R$)

| Cenário | Hardware | 6 TB | 8 TB |
|---|---|---:|---:|
| **A. R430 status quo** | Reaproveita | 0 *(não atende capacidade)* | 0 *(não atende capacidade)* |
| **B. R430 + 4× SSD SATA RAID 10** | Reaproveita | **16.000** | **16.000** (7,68 TB) |
| **B. R430 + RAM 64 GB + SSD RAID 10** | Reaproveita | **16.800** | **16.800** (7,68 TB) |
| **C. R660xs novo + NVMe PCIe 4.0** | Servidor novo | 41.500 | 42.500 |
| D. GCP us-east1 CUD 3 anos | Cloud | 343.890 | ~352.090 |
| E. GCP São Paulo CUD 3 anos | Cloud | ~456.398 | ~466.898 |
| E. GCP São Paulo on-demand | Cloud | ~527.305 | ~537.805 |

### 11.2 Custo amortizado anual em 5 anos (R$/ano)

| Cenário | R$/ano |
|---|---:|
| **B. R430 + SSD enterprise** | **3.200** |
| B. R430 + SSD + RAM 64 GB | 3.360 |
| C. R660xs novo (7,68 TB) | 8.300 |
| D. GCP us-east1 CUD 3 anos | 68.778 |
| E. GCP São Paulo CUD 3 anos | ~91.280 |

### 11.3 Multiplicadores (vs Cenário B mínimo)

| Comparação | Em 5 anos |
|---|---:|
| GCP us-east1 (mais barato) ÷ Cenário B | **≈ 21,5×** |
| GCP São Paulo CUD 3 anos ÷ Cenário B | **≈ 28,5×** |
| GCP São Paulo on-demand ÷ Cenário B | **≈ 33,0×** |
| Cenário C (R660xs) ÷ Cenário B | ≈ 2,6× |

### 11.4 Payback de cada investimento on-premise vs GCP São Paulo CUD 3 anos

GCP SP CUD 3 anos custa R$ 91.280/ano (~R$ 7.607/mês).

| Investimento | Payback (apenas custo evitado de GCP) | Payback combinado (GCP + HH) |
|---|---:|---:|
| Cenário B (R$ 16.000) | **2,1 meses** | **< 1 mês** |
| Cenário B com RAM (R$ 16.800) | **2,2 meses** | **< 1 mês** |
| Cenário C (R$ 41.500) | **5,5 meses** | **3,4 meses** |

### 11.5 Retorno total do investimento (infra + HH) em 5 anos

Consolidando o valor evitado pela escolha do Cenário B vs o cenário pior (GCP São Paulo on-demand) e somando a economia operacional de hora-homem:

| Componente | Valor em 5 anos (R$) |
|---|---:|
| Custo evitado vs GCP São Paulo on-demand | ~527.305 |
| Economia de hora-homem | 165.240 |
| (−) CAPEX Cenário B | (16.800) |
| **Retorno econômico total estimado** | **~675.745** |

Mesmo no cenário cloud mais conservador (GCP us-east1 CUD 3 anos), o retorno consolidado supera **R$ 492.000** em 5 anos.

---

## 12. Ameaças à validade

1. **Assimetria CAPEX vs full-cost cloud:** o cálculo on-premise não inclui energia (~R$ 4.000/ano @ 250 W contínuos), manutenção pós-garantia, refrigeração e mão de obra administrativa. Estimativa preliminar de TCO completo on-premise: R$ 13.000–18.000/ano em 5 anos para o Cenário C; R$ 6.000–9.000/ano para o Cenário B. **Mesmo no cenário menos favorável, on-premise permanece 5–10× mais barato que GCP em SP.**

2. **`pd-ssd` não é PCIe 4.0:** comparação técnica justa exigiria Hyperdisk Extreme, com cobrança extra por IOPS — elevaria o TCO cloud em ~30–40%.

3. **Volatilidade cambial:** o USD/BRL oscilou entre R$ 4,89 e R$ 4,98 em maio/2026. Uma desvalorização do Real de 10% amplifica proporcionalmente o custo da cloud em BRL.

4. **Preços B2B de SSD enterprise no Brasil:** Kingston DC600M e Samsung PM9A3 raramente têm preço público em vitrine. Recomenda-se cotação formal com distribuidores **Officer, Network1, Ingram Micro** antes da submissão para fechar ±5%.

5. **Hyperdisk em São Paulo *(a confirmar)*:** restrições de acesso impediram coleta direta. Validar SKUs `hyperdisk-balanced` e `hyperdisk-extreme` para `southamerica-east1` na Pricing Calculator.

6. **R430 como Proxmox host:** se o servidor sustenta outras VMs além do backup, parte de sua capacidade não pode ser realocada exclusivamente para o vault. Análise de consolidação está **fora do escopo** deste documento.

7. **Fim de suporte Dell para Gen13:** entre 2025 e 2027 conforme contrato. Após esse prazo, peças de reposição passam a ser de mercado secundário. **Aceitável para o Cenário B** dado o payback de 2 meses, mas precisa ser comunicado claramente.

8. **Premissa de quota Google Vault API:** assume-se que limites atuais comportam o volume projetado. Recomenda-se confirmação via console GCP.

9. **Estimativa de hora-homem:** o valor de **R$ 162/h** e a base de **17 h/mês** liberadas são premissas que devem ser validadas com a estrutura de RH (custo HH) e com o registro de tempo do responsável (apontamento de horas). A análise de sensibilidade no horizonte de 5 anos: a variação do HH em ±20% (R$ 130–195/h) altera a economia entre **R$ 132.600 e R$ 198.900**, mantendo o ROI positivo em todas as faixas.

---

## 13. Conclusão e recomendação

O hardware atual (Cenário A) não atende ao requisito de 6–8 TB úteis de storage. Entre as alternativas viáveis:

- O **upgrade do R430 com SSD SATA enterprise (Cenário B)** entrega 7,68 TB úteis com tolerância a falhas, CAPEX único de **R$ 16.800** e payback de **2,2 meses** vs GCP São Paulo (ou inferior a 1 mês considerando também a economia de hora-homem). Aproveita ativos existentes (chassi, CPU, RAM, controladora) e atende plenamente a workload de backup, que é predominantemente I/O sequencial.

- O **investimento em servidor novo R660xs (Cenário C)** entrega 7,68 TB em NVMe PCIe 4.0 com CAPEX de **R$ 41.500** e payback de **5,5 meses**. Apresenta vantagens técnicas (geração 2023, DDR5, PCIe 4.0, garantia estendida) que **não se traduzem em benefício mensurável** para o workload de backup específico, mas oferecem margem de futuro para outras workloads.

- A **migração para GCP (Cenários D e E)** custa entre **R$ 343.890 e R$ 527.305 em 5 anos**, sendo **21 a 33 vezes mais cara** que o Cenário B. Justificável apenas em contextos de elasticidade extrema, eliminação total de infraestrutura física ou requisitos de SLA inalcançáveis on-premise — **nenhum dos quais se aplica** ao workload de backup batch 24/7 da MadeiraMadeira.

Adicionalmente, a **automação do processo libera 204 horas/ano** do time de Plataformas (equivalente a **R$ 33.048/ano** ao custo hora-homem do perfil sênior CLT), o que representa **R$ 165.240 de economia em 5 anos**. Quando combinada com o custo evitado de migração à GCP, o **retorno econômico total estimado supera R$ 675.000 em 5 anos** (Cenário B vs GCP SP on-demand).

**Recomendação:** adotar o **Cenário B — Upgrade do R430 com 4× Kingston DC600M 3,84 TB em RAID 10**, com investimento adicional de R$ 800 em RAM (4× 16 GB DDR4 para totalizar 64 GB e ativar todos os canais de memória da CPU). **CAPEX total recomendado: R$ 16.800.** O Cenário C permanece como opção de upgrade futuro caso surja demanda por consolidação de outras workloads no mesmo chassi.

---

## 14. Próximos passos para submissão do paper

- [ ] Fechar preços de Hyperdisk Balanced/Extreme em `southamerica-east1` via Pricing Calculator oficial
- [ ] Confirmar preço Kingston DC600M 3,84 TB e Samsung PM9A3 com 2–3 distribuidores B2B brasileiros (Officer, Network1, Ingram Micro) para precisão ±5%
- [ ] Estimar TCO completo on-premise (Cenários B e C) incluindo energia, manutenção pós-garantia e mão de obra
- [ ] Validar quota atual da Google Vault API no projeto OAuth utilizado
- [ ] Levantar histórico de uso de CPU/RAM do R430 nos últimos 90 dias (Proxmox metrics) para sustentar premissas da Seção 9
- [ ] Documentar baseline de throughput atual (MB/s sustentado, backups/dia) para comparação pós-upgrade
- [ ] Coletar cotação formal Dell Brasil do R660xs (Cenário C) para fechar ±5%
- [ ] **Confirmar com RH o custo hora-homem efetivo para o perfil de Engenheiro de Plataforma Sênior (Seção 10.3)**
- [ ] **Validar o apontamento de 17 h/mês com registro real de tempo nas tarefas manuais de backup (Seção 10.2)**
- [ ] Adicionar gráficos: curva de custo acumulado por ano dos 5 cenários, distribuição percentual do TCO GCP por componente, comparativo de throughput esperado por cenário, gráfico de payback HH vs infra
- [ ] Revisão por pares dentro do time de Plataformas antes da submissão à direção

---

## 15. Referências

### 15.1 Google Cloud Platform

- <https://cloud.google.com/compute/all-pricing>
- <https://cloud.google.com/compute/disks-image-pricing>
- <https://cloud.google.com/products/compute/pricing/general-purpose>
- <https://cloud.google.com/products/calculator>
- <https://docs.cloud.google.com/compute/docs/instances/signing-up-committed-use-discounts>
- <https://docs.cloud.google.com/compute/docs/disks/hyperdisks>

### 15.2 Hardware Dell

- Dell PowerEdge R660xs — <https://www.dell.com/pt-br/shop/ipovw/poweredge-r660xs>
- Dell PowerEdge R430 (manual técnico) — <https://www.dell.com/support/manuals/poweredge-r430>
- Dell PERC H330 — <https://www.dell.com/support/kbdoc/perc-h330>

### 15.3 SSDs enterprise

- Kingston DC600M — <https://www.kingstonstore.com.br/>
- Samsung PM9A3 — <https://www.samsung.com/us/business/computing/memory-storage/enterprise-solid-state-drives/pm9a3-nvme-u-2-ssd-3-8tb-mz-ql23t800/>
- Samsung PM893 (SATA) — Samsung Business
- Micron 5400 PRO — Micron Technology

### 15.4 Câmbio

- Banco Central do Brasil PTAX — <https://www.bcb.gov.br/>

### 15.5 Distribuidores B2B Brasil

- Officer Distribuidora
- Network1 Distribuição
- Ingram Micro Brasil
- OK Computadores — <https://loja.okcomputadores.com/>
- Trinó Tecnologia — <https://www.trinotecnologia.com.br/>

### 15.6 Hora-homem e referências salariais (Brasil, maio/2026)

- Glassdoor BR — <https://www.glassdoor.com.br/>
- Catho — <https://www.catho.com.br/profissoes/>
- Salário.com.br — <https://www.salario.com.br/>
- Base salarial interna MadeiraMadeira *(referência confidencial, não detalhada neste documento)*

---

## 16. Histórico de revisões

| Versão | Data | Autor | Mudanças |
|---|---|---|---|
| 1.0 | 13/05/2026 | Ivan Campos / Plataformas | Versão inicial consolidando 5 cenários (R430 status quo, R430 upgrade, R660xs novo, GCP us-east1, GCP SP). Pesquisa de preços apoiada por agentes de IA — todos os valores marcados como *(a confirmar)* requerem validação manual antes da submissão. |
| 1.1 | 13/05/2026 | Ivan Campos / Plataformas | Adicionada Seção 10 — Análise econômica do tempo operacional liberado (hora-homem). Premissa: 17 h/mês liberadas × R$ 162/h (perfil Engenheiro de Plataforma Sênior CLT, base interna MadeiraMadeira) = R$ 165.240 de economia em 5 anos. Tabela comparativa atualizada com payback combinado (HH + GCP) e retorno econômico total estimado em R$ 675k. Conclusão revisada. Renumeração das seções 10–15 para 11–16. |
