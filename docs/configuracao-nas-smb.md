# Configuração do Sync via SMB — Servidor → NAS Synology

> Última atualização: 2026-05-25
> Servidor de produção: `10.100.80.10` (hostname `mcta-automacao`)

Este documento descreve como configurar a coleta dos backups finalizados
pelo NAS Synology, lendo a pasta `/mnt/hdd/vault/sync_nas` do servidor via **SMB**.

---

## 1. Visão geral do fluxo

O servidor **não envia** os backups pela rede. Ele apenas deixa o ZIP
finalizado em disco e cria um marcador. O **NAS é quem puxa** os arquivos,
montando a pasta do servidor via SMB. Isso elimina o gargalo da quota de
750 GB/dia do Google Drive (ver `servicos/nas_sync.py`).

```
┌─ SERVIDOR (10.100.80.10) ────────────────────┐      ┌─ NAS Synology ──────────────┐
│ Backup finaliza →                             │      │ Tarefa agendada (a cada Xh): │
│   MOVE → /mnt/hdd/vault/sync_nas/X.zip        │◄─SMB─│  1. monta //10.100.80.10/    │
│ status do backup = "aguardando_nas"           │ :445 │     sync_nas                 │
│                                               │      │  2. sincroniza os *.zip      │
│ finalizacao_nas.py: após 6h fecha ticket      │      │     para o storage do NAS    │
│   Jira + deleta conta + APAGA o ZIP local     │      └──────────────────────────────┘
│ limpeza.py (boot): safety-net — apaga ZIPs    │
│   órfãos com mais de NAS_SYNC_RETENCAO_HORAS  │
└───────────────────────────────────────────────┘
```

**Sem marcadores.** O servidor apenas move o `.zip` para a pasta; não cria
nem renomeia arquivos de controle. O NAS sincroniza a pasta por conta própria.
A exclusão da cópia local é responsabilidade do servidor:

| Quando | O que apaga o ZIP local |
|---|---|
| Após a janela `NAS_SYNC_HORAS_ESPERA` (6h) | `finalizacao_nas.py`, ao fechar o ciclo do backup |
| No boot do servidor (safety-net) | `limpeza.py`, para ZIPs órfãos com mais de `NAS_SYNC_RETENCAO_HORAS` (6h) |

> O usuário SMB do NAS só precisa de **leitura** para sincronizar os `.zip`
> (não há mais markers para renomear). O share permanece com escrita habilitada
> por conveniência, mas ela não é mais exigida pelo fluxo.

---

## 2. Lado servidor (`10.100.80.10`) — exportar a pasta via Samba

Executar como `root` (ou via `sudo`). O container roda como **UID 1000**
(`infra`), então a pasta deve pertencer a esse UID.

### 2.1. Criar a pasta de sync (se ainda não existir)

```bash
sudo mkdir -p /mnt/hdd/vault/sync_nas
sudo chown 1000:1000 /mnt/hdd/vault/sync_nas
sudo chmod 750 /mnt/hdd/vault/sync_nas
```

### 2.2. Instalar o Samba

```bash
sudo apt-get update && sudo apt-get install -y samba
```

### 2.3. Definir a senha SMB do usuário `infra`

A implantação usa o usuário **`infra`** (UID 1000, dono da pasta e do container),
com a **mesma senha de login** do servidor. O Samba mantém um banco de senhas
próprio, então é preciso registrá-la:

```bash
sudo smbpasswd -a infra      # define a senha SMB (use a mesma senha do servidor)
sudo smbpasswd -e infra      # habilita o usuário
```

> Como `infra` já é dono de `/mnt/hdd/vault/sync_nas` (UID 1000), não há ajuste
> extra de permissão — ele lê e sincroniza os `.zip` normalmente.

### 2.4. Configurar o compartilhamento

Adicionar ao final de `/etc/samba/smb.conf`:

```ini
[sync_nas]
   comment = Backups Workspace aguardando coleta pelo NAS
   path = /mnt/hdd/vault/sync_nas
   browseable = no
   read only = no
   guest ok = no
   valid users = infra
   create mask = 0660
   directory mask = 0770
   force user = infra
```

Validar e reiniciar:

```bash
sudo testparm                # confere a sintaxe
sudo systemctl enable --now smbd
sudo systemctl restart smbd
```

### 2.5. Firewall — liberar TCP 445 **apenas** para o IP do NAS

Substitua `IP_DO_NAS` pelo IP real do Synology.

```bash
sudo ufw allow from IP_DO_NAS to any port 445 proto tcp
# (se o ufw não estiver em uso, aplicar regra equivalente no firewall vigente)
```

> Não exponha a porta 445 para a rede toda — restrinja ao IP do NAS
> (boas práticas de Rede e Exposição do CLOUD.md).

---

## 3. Lado NAS Synology (DSM) — montar e coletar

### 3.1. Pasta de destino e credenciais

1. Crie/escolha a pasta de destino no NAS, ex.: `/volume1/Backups-Workspace`.
2. Crie o arquivo de credenciais SMB (lido pelo script), com permissão `0600`:

```bash
# via SSH no NAS, como root:
cat > /volume1/Backups-Workspace/.smbcred <<'EOF'
username=infra
password=SENHA_FORTE
EOF
chmod 600 /volume1/Backups-Workspace/.smbcred
```

### 3.2. Instalar o script de coleta

Copie `scripts/synology/coletar_backups.sh` (deste repositório) para o NAS,
ex.: `/volume1/Backups-Workspace/coletar_backups.sh`, e ajuste o bloco
**CONFIGURAÇÃO** no topo (IP, destino, caminho do `.smbcred`). Torne executável:

```bash
chmod +x /volume1/Backups-Workspace/coletar_backups.sh
```

### 3.3. Agendar no DSM

**Painel de Controle → Agendador de Tarefas → Criar → Tarefa Agendada → Script definido pelo usuário**

- **Usuário:** `root` (necessário para `mount -t cifs`).
- **Agendamento:** sugerido a cada **1–2 horas** (a janela de finalização do
  servidor é de 6h — ver `finalizacao_nas.py` — então essa folga é ampla).
- **Comando:** sincronizar o conteúdo da pasta montada para o storage do NAS,
  por exemplo:
  ```bash
  rsync -a /mnt/sync_nas/ /volume1/Backups-Workspace/
  ```

> ⚠️ O servidor **não usa mais markers** `.ready`/`.uploaded`. A coleta agora é
> uma simples sincronização dos arquivos `.zip` da pasta. O antigo script
> `coletar_backups.sh` (validação de SHA256 + renomeação de marker) **não é mais
> necessário** — basta o NAS espelhar a pasta enquanto os `.zip` existirem.
> Lembre-se: o servidor apaga o ZIP local **6h** após disponibilizá-lo, então o
> agendamento do NAS precisa rodar dentro dessa janela.

### 3.4. Teste manual

```bash
# via SSH no NAS, após montar a pasta:
rsync -a --dry-run /mnt/sync_nas/ /volume1/Backups-Workspace/
ls -la /volume1/Backups-Workspace/*/
```

---

## 4. Validação ponta a ponta

1. **Servidor:** processar um backup pequeno → confirmar que aparece
   `/mnt/hdd/vault/sync_nas/X.zip` (direto na raiz, sem subpasta) e que o status
   do registro é `aguardando_nas`.
2. **NAS:** rodar a sincronização (ou aguardar o agendamento) → confirmar o ZIP
   em `/volume1/Backups-Workspace/`.
3. **Servidor:** após 6h, `finalizacao_nas.py` fecha o ticket no Jira, (quando
   aplicável) exclui a conta Workspace e **apaga o ZIP local**.
4. **Servidor:** ZIPs órfãos (sem registro no banco) são apagados no boot pela
   `limpeza.py` após `NAS_SYNC_RETENCAO_HORAS` (padrão 6).

## 5. Solução de problemas

| Sintoma | Causa provável | Ação |
|---|---|---|
| `mount error(13)` no NAS | usuário/senha SMB errados ou usuário desabilitado | revisar `.smbcred` e `smbpasswd -e infra` |
| `mount error(115)`/timeout | firewall bloqueando 445 ou `smbd` parado | revisar regra do firewall e `systemctl status smbd` |
| ZIP some antes de o NAS coletar | agendamento do NAS mais lento que a janela de 6h | aumentar a frequência da tarefa no DSM ou subir `NAS_SYNC_HORAS_ESPERA` no `.env` |
| ZIPs antigos acumulando na pasta | NAS não está sincronizando | revisar a tarefa agendada do DSM e a montagem SMB |

---

Relacionado: `servicos/nas_sync.py`, `processamento/finalizacao_nas.py`,
`processamento/limpeza.py`, `docs/instalacao-ubuntu-servidor.md`.
