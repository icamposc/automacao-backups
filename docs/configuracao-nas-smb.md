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
│   MOVE → /mnt/hdd/vault/sync_nas/<email>/X.zip      │      │  1. monta //10.100.80.10/    │
│   cria  → /mnt/hdd/vault/sync_nas/<email>/X.zip.ready│◄─SMB─│     sync_nas                 │
│          (conteúdo = SHA256 do ZIP)           │ :445 │  2. copia *.ready + .zip     │
│ status do backup = "aguardando_nas"           │      │  3. valida SHA256            │
│                                               │      │  4. renomeia .ready→.uploaded│
│ limpeza.py: vê .uploaded → após 7 dias apaga  │      │     (precisa de ESCRITA)     │
│   ZIP+marker; alerta .ready "stale"           │      └──────────────────────────────┘
│ finalizacao_nas.py: após 23h fecha ticket     │
│   Jira + deleta conta Workspace               │
└───────────────────────────────────────────────┘
```

**Handshake por marcadores** (exigido por `processamento/limpeza.py`):

| Marcador | Quem cria | Significado |
|---|---|---|
| `X.zip.ready` | servidor | ZIP pronto para coleta; conteúdo = SHA256 esperado |
| `X.zip.uploaded` | **NAS** | NAS copiou e validou; servidor pode limpar após 7 dias |

> ⚠️ O usuário SMB do NAS **precisa de permissão de escrita** no share,
> pois é ele quem renomeia `.ready` → `.uploaded`.

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
> extra de permissão — ele lê, escreve e renomeia os markers normalmente.

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
  servidor é de 23h — ver `finalizacao_nas.py` — então essa folga é ampla).
- **Comando:**
  ```bash
  /volume1/Backups-Workspace/coletar_backups.sh
  ```

### 3.4. Teste manual

```bash
# via SSH no NAS:
/volume1/Backups-Workspace/coletar_backups.sh
tail -n 30 /volume1/Backups-Workspace/coleta.log
```

O log deve mostrar `Coleta finalizada: total=N ok=N falhas=0`. Confirme que,
no servidor, os markers viraram `.uploaded`:

```bash
# no servidor:
ls -la /mnt/hdd/vault/sync_nas/*/
```

---

## 4. Validação ponta a ponta

1. **Servidor:** processar um backup pequeno → confirmar que aparece
   `/mnt/hdd/vault/sync_nas/<email>/X.zip` + `X.zip.ready` e que o status do
   registro é `aguardando_nas`.
2. **NAS:** rodar o script (ou aguardar o agendamento) → confirmar o ZIP em
   `/volume1/Backups-Workspace/<email>/` e o marcador virando `.uploaded`.
3. **Servidor:** após 23h, `finalizacao_nas.py` fecha o ticket no Jira e
   (quando aplicável) exclui a conta Workspace.
4. **Servidor:** após `NAS_SYNC_RETENCAO_DIAS` (padrão 7), `limpeza.py`
   apaga o ZIP local e o marcador `.uploaded`.

## 5. Solução de problemas

| Sintoma | Causa provável | Ação |
|---|---|---|
| `mount error(13)` no NAS | usuário/senha SMB errados ou usuário desabilitado | revisar `.smbcred` e `smbpasswd -e infra` |
| `mount error(115)`/timeout | firewall bloqueando 445 ou `smbd` parado | revisar regra do firewall e `systemctl status smbd` |
| markers `.ready` antigos acumulando | NAS não está coletando | ver alerta de "stale" nos logs do servidor (`limpeza.py`); revisar a tarefa agendada do DSM |
| SHA256 divergente no log | cópia corrompida/incompleta | o script descarta a cópia e mantém `.ready`; será recoletado |
| NAS copia mas não renomeia `.ready` | usuário SMB sem escrita | revisar `valid users`/`force user` e permissões da pasta (seção 2.3) |

---

Relacionado: `servicos/nas_sync.py`, `processamento/finalizacao_nas.py`,
`processamento/limpeza.py`, `docs/instalacao-ubuntu-servidor.md`.
