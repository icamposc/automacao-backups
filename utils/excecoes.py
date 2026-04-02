"""
============================================================
Exceções Personalizadas — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-04-02
Descrição: Hierarquia de exceções específicas para cada
           etapa do fluxo de backup. Permite que o
           orquestrador identifique onde ocorreu o erro
           e envie o alerta correto ao Google Chat.
============================================================
"""


class ErroBackup(Exception):
    """Exceção base para todos os erros do fluxo de backup."""


class ErroVaultTimeout(ErroBackup):
    """Export do Google Vault não completou dentro do tempo máximo."""


class ErroVaultFalha(ErroBackup):
    """Export do Google Vault retornou status FAILED."""


class ErroDownload(ErroBackup):
    """Falha ao baixar arquivos exportados do Cloud Storage."""


class ErroUpload(ErroBackup):
    """Falha ao fazer upload do ZIP para o Google Drive."""


class ErroExclusaoConta(ErroBackup):
    """Falha ao excluir a conta do colaborador no Google Workspace."""


class ErroJira(ErroBackup):
    """Falha na integração com o Jira (comentário, transição ou formulário)."""
