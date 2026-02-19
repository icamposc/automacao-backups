"""
============================================================
Simulador de Webhook — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-02-19
Descrição: Script para simular o envio de um webhook do Jira
           localmente, útil para testar o fluxo completo sem
           depender do Jira real.

           Uso: python -m testes.simular_webhook
============================================================
Histórico:
  1.0.0 (2026-02-19) — Versão inicial
============================================================
"""

import requests
import json
import sys

# URL do servidor local (ajuste a porta conforme o .env)
URL_SERVIDOR = "http://localhost:5000"


def simular_webhook(email: str, ticket_id: str, nome: str = None):
    """
    Envia uma requisição POST simulando o webhook do Jira.

    Args:
        email: E-mail do colaborador para teste
        ticket_id: Chave do ticket simulado
        nome: Nome do colaborador (opcional)
    """
    url = f"{URL_SERVIDOR}/webhook/backup-desligado"

    # Payload simulando o que o "Automation for Jira" enviaria
    payload = {
        "email_colaborador": email,
        "ticket_id": ticket_id,
    }

    # Adiciona o nome se fornecido
    if nome:
        payload["nome_colaborador"] = nome

    print(f"{'=' * 60}")
    print(f"Simulador de Webhook — Automação de Backups")
    print(f"{'=' * 60}")
    print(f"URL: {url}")
    print(f"Payload: {json.dumps(payload, indent=2, ensure_ascii=False)}")
    print(f"{'=' * 60}")

    try:
        # Envia a requisição POST
        resposta = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )

        print(f"\nStatus HTTP: {resposta.status_code}")
        print(f"Resposta: {json.dumps(resposta.json(), indent=2, ensure_ascii=False)}")

        if resposta.status_code == 200:
            print("\n✓ Webhook enviado com sucesso!")
            print("  Acompanhe o processamento nos logs do servidor.")
        else:
            print(f"\n✗ Erro ao enviar webhook: HTTP {resposta.status_code}")

    except requests.exceptions.ConnectionError:
        print(f"\n✗ ERRO: Não foi possível conectar ao servidor em {URL_SERVIDOR}")
        print("  Verifique se o servidor está rodando.")
        print("  Inicie com: python -m app.servidor")

    except Exception as erro:
        print(f"\n✗ ERRO: {erro}")


def verificar_saude():
    """Verifica se o servidor está rodando (health check)."""
    url = f"{URL_SERVIDOR}/saude"

    try:
        resposta = requests.get(url, timeout=5)
        print(f"Health Check: {resposta.json()}")
        return resposta.status_code == 200
    except requests.exceptions.ConnectionError:
        print(f"Servidor não está rodando em {URL_SERVIDOR}")
        return False


if __name__ == "__main__":
    # Verifica se o servidor está ativo antes de enviar
    print("Verificando conexão com o servidor...\n")
    if not verificar_saude():
        print("\nServidor não disponível. Abortando simulação.")
        sys.exit(1)

    print()

    # Dados de teste — altere conforme necessário
    EMAIL_TESTE = "colaborador.teste@empresa.com"
    TICKET_TESTE = "SPN-999"
    NOME_TESTE = "Colaborador de Teste"

    # Se argumentos foram passados pela linha de comando, usa eles
    if len(sys.argv) >= 3:
        EMAIL_TESTE = sys.argv[1]
        TICKET_TESTE = sys.argv[2]
        NOME_TESTE = sys.argv[3] if len(sys.argv) >= 4 else None

    simular_webhook(EMAIL_TESTE, TICKET_TESTE, NOME_TESTE)
