# ============================================================
# Dockerfile — Automação de Backups
# ============================================================
# Imagem única usada tanto pelo servidor (Gunicorn) quanto
# pelo worker (Celery). O comando é definido no docker-compose.
# ============================================================

FROM python:3.11-slim

# Evita arquivos .pyc e garante saída imediata nos logs
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Dependências mínimas do sistema operacional
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Instala dependências Python antes de copiar o código
# (aproveita o cache do Docker se requirements.txt não mudar)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código da aplicação
COPY . .

# Cria diretórios necessários em tempo de build
RUN mkdir -p logs temp storage

# Usuário não-root para segurança
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Porta exposta pelo servidor Flask/Gunicorn
EXPOSE 5000
