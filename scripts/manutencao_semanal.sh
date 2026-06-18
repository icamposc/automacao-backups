#!/usr/bin/env bash
# ============================================================
# Manutenção semanal — Automação de Backups
# ============================================================
# Reinício controlado dos containers de APLICAÇÃO (servidor + worker),
# agendado para domingo 04h (horário de Brasília) via systemd timer.
#
# GUARDA: NÃO reinicia se houver backup em processamento (status
# 'em_andamento') — adia para a próxima janela. Evita repetir o cenário
# do incidente SPN-64951 (reinício com backup ativo).
#
# Não reinicia o Redis (broker) — apenas servidor e worker.
# ============================================================
set -u
LOG="/var/log/automacao-backups-manutencao.log"
COMPOSE="/opt/automacao-backups/deploy/docker-compose.yml"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

log "=== Manutenção semanal iniciada ==="

ATIVOS=$(docker exec automacao_backups_servidor python3 -c "import sqlite3;c=sqlite3.connect('/app/storage/backups.db');print(c.execute(\"SELECT COUNT(*) FROM backups WHERE status_geral='em_andamento'\").fetchone()[0])" 2>/dev/null)

if ! [[ "$ATIVOS" =~ ^[0-9]+$ ]]; then
  log "Não foi possível consultar o banco (ret='$ATIVOS') — ABORTANDO sem reiniciar (seguro)."
  exit 0
fi

if [ "$ATIVOS" -ne 0 ]; then
  log "$ATIVOS backup(s) em andamento — reinício ADIADO para a próxima janela."
  exit 0
fi

log "Sistema ocioso — reiniciando containers servidor + worker..."
if docker compose -f "$COMPOSE" restart servidor worker >>"$LOG" 2>&1; then
  log "Reinício concluído com sucesso."
else
  log "ERRO no reinício — verificar manualmente."
fi
