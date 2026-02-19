#!/bin/bash
# ============================================================
# Script de Inicialização — Automação de Backups
# ============================================================
# Versão: 1.0.0
# Data: 2026-02-19
# Descrição: Inicia o servidor usando Gunicorn (produção)
#            ou Flask (desenvolvimento).
#
# Uso:
#   ./scripts/iniciar_servidor.sh            → Produção (Gunicorn)
#   ./scripts/iniciar_servidor.sh --dev      → Desenvolvimento (Flask)
#
# Pré-requisitos:
#   - Python 3.10+ instalado
#   - Dependências instaladas: pip install -r requirements.txt
#   - Arquivo .env configurado na raiz do projeto
# ============================================================
# Histórico:
#   1.0.0 (2026-02-19) — Versão inicial
# ============================================================

# Diretório raiz do projeto (um nível acima de /scripts)
RAIZ_PROJETO="$(cd "$(dirname "$0")/.." && pwd)"

echo "============================================================"
echo "Automação de Backups — Servidor"
echo "============================================================"
echo "Diretório do projeto: $RAIZ_PROJETO"

# Navega para o diretório do projeto
cd "$RAIZ_PROJETO" || exit 1

# Verifica se o arquivo .env existe
if [ ! -f ".env" ]; then
    echo ""
    echo "[ERRO] Arquivo .env não encontrado!"
    echo "  → Copie o arquivo de exemplo: cp .env.example .env"
    echo "  → Preencha as variáveis com os valores reais"
    exit 1
fi

# Verifica se o Python está instalado
if ! command -v python3 &> /dev/null; then
    echo ""
    echo "[ERRO] Python 3 não encontrado!"
    echo "  → Instale o Python 3.10 ou superior"
    exit 1
fi

# Verifica se as dependências estão instaladas
if ! python3 -c "import flask" &> /dev/null; then
    echo ""
    echo "[AVISO] Dependências não instaladas. Instalando..."
    pip3 install -r requirements.txt
fi

# Cria pastas necessárias
mkdir -p logs temp

echo ""

# Modo de execução
if [ "$1" = "--dev" ]; then
    # --- MODO DESENVOLVIMENTO ---
    echo "Modo: DESENVOLVIMENTO (Flask)"
    echo "  → Debug habilitado, recarrega automaticamente ao alterar código"
    echo ""
    python3 -m app.servidor
else
    # --- MODO PRODUÇÃO ---
    # Porta padrão ou do .env
    PORTA=$(grep SERVIDOR_PORTA .env | cut -d '=' -f2 | tr -d ' ' || echo "5000")

    echo "Modo: PRODUÇÃO (Gunicorn)"
    echo "  → Workers: 2 (adequado para I/O bound)"
    echo "  → Porta: $PORTA"
    echo "  → Timeout: 120s"
    echo ""

    # Inicia o Gunicorn com configurações de produção
    # --workers 2: Dois processos worker (backup é I/O bound, não precisa de muitos)
    # --timeout 120: Timeout de 120s para requisições HTTP
    # --access-logfile: Log de acessos HTTP
    # --error-logfile: Log de erros do Gunicorn
    gunicorn \
        --bind "0.0.0.0:$PORTA" \
        --workers 2 \
        --timeout 120 \
        --access-logfile "logs/gunicorn_acesso.log" \
        --error-logfile "logs/gunicorn_erro.log" \
        --log-level info \
        "app.servidor:app"
fi
