#!/bin/bash
# ============================================================
# Script de Inicialização do Worker Celery — Automação de Backups
# ============================================================
# Versão: 1.0.0
# Data: 2026-04-02
# Descrição: Inicia o worker Celery que processa as tarefas
#            de backup de forma assíncrona.
#
# Pré-requisitos:
#   - Redis rodando (redis-server)
#   - REDIS_URL configurado no .env
#   - pip install -r requirements.txt
#
# Uso:
#   ./scripts/iniciar_worker.sh
#   ./scripts/iniciar_worker.sh --dev   → Log colorido, sem detach
# ============================================================

RAIZ_PROJETO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$RAIZ_PROJETO" || exit 1

echo "============================================================"
echo "Automação de Backups — Worker Celery"
echo "============================================================"
echo "Diretório do projeto: $RAIZ_PROJETO"

# Verifica Redis
if ! command -v redis-cli &> /dev/null; then
    echo ""
    echo "[AVISO] redis-cli não encontrado. Verifique se o Redis está instalado."
fi

# Verifica conectividade com Redis
REDIS_URL=$(grep REDIS_URL .env | cut -d '=' -f2 | tr -d ' ' || echo "redis://localhost:6379/0")
if redis-cli -u "$REDIS_URL" ping 2>/dev/null | grep -q PONG; then
    echo "Redis: conectado ($REDIS_URL)"
else
    echo ""
    echo "[ERRO] Não foi possível conectar ao Redis: $REDIS_URL"
    echo "  → Inicie o Redis: redis-server"
    exit 1
fi

echo ""

if [ "$1" = "--dev" ]; then
    # Modo desenvolvimento: log colorido, sem detach
    echo "Modo: DESENVOLVIMENTO (log no terminal)"
    echo ""
    celery -A worker.celery_app worker \
        --loglevel=info \
        --concurrency=1 \
        --queues=celery \
        --hostname="worker@%h"
else
    # Modo produção: log em arquivo
    mkdir -p logs

    echo "Modo: PRODUÇÃO"
    echo "  → Concurrency: 1 (respeita limite de exports do Google Vault)"
    echo "  → Logs: logs/celery_worker.log"
    echo ""

    celery -A worker.celery_app worker \
        --loglevel=info \
        --concurrency=1 \
        --queues=celery \
        --hostname="worker@%h" \
        --logfile="logs/celery_worker.log" \
        --detach \
        --pidfile="logs/celery_worker.pid"

    echo "Worker iniciado em background."
    echo "  → Para parar: celery -A worker.celery_app control shutdown"
    echo "  → Para monitorar: tail -f logs/celery_worker.log"
fi
