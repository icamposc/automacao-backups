#!/bin/bash
# ============================================================
# Coleta de Backups do Servidor → NAS Synology
# ============================================================
# Versão: 1.0.0
# Data: 2026-05-25
# Onde roda: NAS Synology (DSM → Painel de Controle → Agendador
#            de Tarefas → Tarefa Agendada → Script definido pelo
#            usuário, executado como root).
#
# O que faz (lado "pull" do fluxo descrito em servicos/nas_sync.py):
#   1. Monta via SMB o compartilhamento sync_nas do servidor.
#   2. Varre <share>/<email>/<arquivo>.zip.ready.
#   3. Para cada marker .ready:
#        a. Lê o SHA256 esperado (conteúdo do marker).
#        b. Copia o .zip para o NAS preservando a subpasta <email>/.
#        c. Recalcula o SHA256 do arquivo copiado e compara.
#        d. Se confere → renomeia o marker remoto .ready → .uploaded
#           (sinaliza ao servidor que pode limpar após a retenção).
#        e. Se NÃO confere → remove a cópia parcial e mantém o .ready
#           (o servidor alerta markers .ready "stale" — ver limpeza.py).
#   4. Desmonta o compartilhamento.
#
# Pré-requisitos no NAS:
#   - sha256sum disponível (DSM padrão).
#   - Pasta de destino DESTINO_NAS já existente e gravável.
#   - Usuário SMB dedicado com escrita no share (necessário para
#     renomear o marker .ready → .uploaded).
#
# Configuração: edite o bloco "CONFIGURAÇÃO" abaixo. As credenciais
# SMB devem ficar num arquivo 0600 (CRED_SMB), não neste script.
# ============================================================

set -u

# ─────────────── CONFIGURAÇÃO ───────────────
SERVIDOR_IP="10.100.80.10"          # IP do servidor de produção
SHARE="sync_nas"                    # nome do compartilhamento Samba
MOUNT_POINT="/tmp/mnt_sync_nas"     # ponto de montagem temporário no NAS
DESTINO_NAS="/volume1/Backups-Workspace"   # AJUSTE: pasta final no NAS
CRED_SMB="/volume1/Backups-Workspace/.smbcred"  # arquivo 0600: ver doc
SMB_VERS="3.0"                      # versão do protocolo SMB
LOG="/volume1/Backups-Workspace/coleta.log"
LOCK="/tmp/coletar_backups.lock"
# ────────────────────────────────────────────

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [$1] ${*:2}" | tee -a "$LOG"; }

# Evita execuções concorrentes (a tarefa agendada pode sobrepor).
if [ -e "$LOCK" ]; then
    log WARN "Já existe uma coleta em andamento (lock $LOCK). Saindo."
    exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null; umount "$MOUNT_POINT" 2>/dev/null' EXIT
if ! mkdir "$LOCK" 2>/dev/null; then
    log WARN "Não consegui criar o lock. Saindo."
    exit 0
fi

mkdir -p "$MOUNT_POINT" "$DESTINO_NAS"

# Monta o share do servidor (somente para esta execução).
if mountpoint -q "$MOUNT_POINT"; then
    umount "$MOUNT_POINT" 2>/dev/null
fi
if ! mount -t cifs "//${SERVIDOR_IP}/${SHARE}" "$MOUNT_POINT" \
        -o "credentials=${CRED_SMB},vers=${SMB_VERS},iocharset=utf8,uid=0,gid=0"; then
    log ERRO "Falha ao montar //${SERVIDOR_IP}/${SHARE} em ${MOUNT_POINT}."
    exit 1
fi
log INFO "Montado //${SERVIDOR_IP}/${SHARE}. Procurando markers .ready..."

total=0; ok=0; falhas=0

# Itera sobre todos os markers .ready (em subpastas por email).
while IFS= read -r marker; do
    total=$((total + 1))
    zip_origem="${marker%.ready}"            # remove sufixo .ready → .zip
    nome_zip="$(basename "$zip_origem")"
    subpasta="$(basename "$(dirname "$zip_origem")")"  # <email>
    destino_dir="${DESTINO_NAS}/${subpasta}"
    destino_zip="${destino_dir}/${nome_zip}"

    if [ ! -f "$zip_origem" ]; then
        log ERRO "Marker sem ZIP correspondente: $marker"
        falhas=$((falhas + 1))
        continue
    fi

    sha_esperado="$(tr -d '[:space:]' < "$marker")"
    mkdir -p "$destino_dir"

    log INFO "Copiando $subpasta/$nome_zip ..."
    if ! cp -f "$zip_origem" "$destino_zip"; then
        log ERRO "Falha ao copiar $nome_zip — mantendo .ready."
        rm -f "$destino_zip" 2>/dev/null
        falhas=$((falhas + 1))
        continue
    fi

    sha_copia="$(sha256sum "$destino_zip" | awk '{print $1}')"

    if [ -n "$sha_esperado" ] && [ "$sha_esperado" != "$sha_copia" ]; then
        log ERRO "SHA256 divergente em $nome_zip (esperado=$sha_esperado obtido=$sha_copia) — removendo cópia e mantendo .ready."
        rm -f "$destino_zip"
        falhas=$((falhas + 1))
        continue
    fi
    if [ -z "$sha_esperado" ]; then
        log WARN "Marker $nome_zip sem SHA256 — cópia aceita sem verificação."
    fi

    # Handshake: renomeia o marker remoto .ready → .uploaded.
    if mv -f "$marker" "${marker%.ready}.uploaded"; then
        log INFO "OK: $nome_zip coletado e marcado .uploaded."
        ok=$((ok + 1))
    else
        log ERRO "Cópia OK mas falhou ao renomear o marker de $nome_zip — será recoletado na próxima execução."
        rm -f "$destino_zip"
        falhas=$((falhas + 1))
    fi
done < <(find "$MOUNT_POINT" -type f -name '*.ready' 2>/dev/null)

log INFO "Coleta finalizada: total=$total ok=$ok falhas=$falhas"
exit 0
