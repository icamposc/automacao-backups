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

    def __init__(self, mensagem: str, stats: dict = None):
        super().__init__(mensagem)
        self.stats = stats or {}


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


class ErroEspacoInsuficiente(ErroBackup):
    """O Vault export é maior do que o espaço livre em PASTA_VAULT.

    Detectado no pre-flight da Etapa 4 (download). Evita iniciar
    downloads que vão necessariamente falhar por disco cheio,
    poupando horas de I/O em vão e o resíduo parcial em /mnt/hdd.
    """

    def __init__(self, mensagem: str, necessario_gb: float = 0, disponivel_gb: float = 0):
        super().__init__(mensagem)
        self.necessario_gb = necessario_gb
        self.disponivel_gb = disponivel_gb


class ErroRecuperacaoBloqueada(ErroBackup):
    """Ticket excedeu o limite de tentativas de recuperação automática.

    Detectado em recuperacao.py: se o ticket já falhou N vezes
    seguidas, não re-enfileira (evita loop de reprocessamento
    quando a causa raiz persiste).
    """
