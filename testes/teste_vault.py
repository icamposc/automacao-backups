"""
============================================================
Testes do Google Vault — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-02-19
Descrição: Testes de integração para o módulo vault_exportacao.
           Estes testes requerem credenciais válidas e acesso
           ao Google Vault para funcionar.

           Uso: pytest testes/teste_vault.py -v
           (requer .env configurado e Service Account válida)
============================================================
Histórico:
  1.0.0 (2026-02-19) — Versão inicial
============================================================
"""

import pytest
from pathlib import Path


class TestVaultExportacao:
    """
    Testes de integração para exportações do Google Vault.

    ATENÇÃO: Estes testes criam exportações REAIS no Google Vault.
    Use apenas com um e-mail de teste e em ambiente controlado.
    """

    # E-mail de teste — altere para um e-mail válido no seu domínio
    EMAIL_TESTE = "teste.backup@exemplo.com.br"

    @pytest.mark.skipif(
        not Path(".env").exists(),
        reason="Arquivo .env não encontrado — testes de integração requerem configuração",
    )
    def test_criar_exportacao_email(self):
        """Testa a criação de uma exportação de e-mail no Vault."""
        from servicos.vault_exportacao import criar_exportacao_email

        resultado = criar_exportacao_email(self.EMAIL_TESTE)

        # Verifica que a exportação foi criada
        assert resultado is not None
        assert "id" in resultado
        assert resultado.get("status") in ["IN_PROGRESS", "COMPLETED"]
        print(f"Export de e-mail criado — ID: {resultado['id']}")

    @pytest.mark.skipif(
        not Path(".env").exists(),
        reason="Arquivo .env não encontrado — testes de integração requerem configuração",
    )
    def test_criar_exportacao_drive(self):
        """Testa a criação de uma exportação de Drive no Vault."""
        from servicos.vault_exportacao import criar_exportacao_drive

        resultado = criar_exportacao_drive(self.EMAIL_TESTE)

        # Verifica que a exportação foi criada
        assert resultado is not None
        assert "id" in resultado
        assert resultado.get("status") in ["IN_PROGRESS", "COMPLETED"]
        print(f"Export de Drive criado — ID: {resultado['id']}")
