"""
============================================================
Módulo de Exclusão de Conta — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-03-10
Descrição: Gerencia a exclusão da conta Google Workspace de
           colaboradores desligados. Antes de excluir, verifica
           se o arquivo de backup existe no Drive Compartilhado
           (status 200), garantindo que a exclusão só ocorra
           após confirmação do upload bem-sucedido.
============================================================
Histórico:
  1.0.0 (2026-03-10) — Versão inicial
============================================================
"""

from googleapiclient.errors import HttpError

from servicos.google_auth import obter_servico_drive, obter_servico_admin
from config.configuracoes import DRIVE_PASTA_DESTINO_ID
from utils.logger import obter_logger

logger = obter_logger("conta_exclusao")


def verificar_arquivo_no_drive(arquivo_id: str) -> bool:
    """
    Verifica se o arquivo de backup existe no Drive Compartilhado.

    Faz uma requisição GET para o arquivo usando o ID retornado
    pelo upload. Se a resposta for bem-sucedida (status 200),
    confirma que o arquivo está acessível no Drive.

    Args:
        arquivo_id: ID do arquivo no Google Drive (retornado pelo upload)

    Returns:
        True se o arquivo existe e está acessível no Drive Compartilhado

    Raises:
        Exception: Se o arquivo não for encontrado ou não estiver acessível
    """
    logger.info(f"Verificando existência do arquivo no Drive Compartilhado (ID: {arquivo_id})...")

    try:
        servico = obter_servico_drive()

        # Busca o arquivo pelo ID no Drive Compartilhado
        # supportsAllDrives=True é obrigatório para Shared Drives
        resposta = servico.files().get(
            fileId=arquivo_id,
            fields="id, name, parents, mimeType",
            supportsAllDrives=True,
        ).execute()

        nome_arquivo = resposta.get("name", "desconhecido")
        parents = resposta.get("parents", [])

        # Verifica se o arquivo está na pasta de destino correta
        if DRIVE_PASTA_DESTINO_ID not in parents:
            raise Exception(
                f"Arquivo '{nome_arquivo}' encontrado, mas não está na pasta "
                f"de destino esperada ({DRIVE_PASTA_DESTINO_ID})"
            )

        logger.info(
            f"Arquivo confirmado no Drive Compartilhado: "
            f"'{nome_arquivo}' (ID: {arquivo_id})"
        )
        return True

    except HttpError as erro:
        if erro.resp.status == 404:
            raise Exception(
                f"Arquivo de backup não encontrado no Drive Compartilhado "
                f"(ID: {arquivo_id}). A conta NÃO será excluída."
            )
        raise Exception(
            f"Erro ao verificar arquivo no Drive (HTTP {erro.resp.status}): {erro}"
        )


def deletar_conta(email: str) -> dict:
    """
    Deleta a conta do colaborador no Google Workspace.

    Utiliza a API Admin Directory (v1) para excluir permanentemente
    a conta do usuário. Esta operação é IRREVERSÍVEL.

    IMPORTANTE: Esta função só deve ser chamada APÓS a confirmação
    de que o backup foi enviado com sucesso para o Drive Compartilhado.

    Args:
        email: E-mail do colaborador cuja conta será excluída

    Returns:
        Dicionário com informações da exclusão:
        - 'email': E-mail da conta excluída
        - 'status': Status da operação ('excluida')

    Raises:
        Exception: Se a exclusão falhar
    """
    logger.info(f"Iniciando exclusão da conta Google Workspace: {email}")

    try:
        servico = obter_servico_admin(email)

        # Deleta a conta do usuário — operação irreversível
        servico.users().delete(userKey=email).execute()

        logger.info(f"Conta excluída com sucesso: {email}")

        return {
            "email": email,
            "status": "excluida",
        }

    except HttpError as erro:
        if erro.resp.status == 404:
            raise Exception(
                f"Conta não encontrada no Google Workspace: {email}. "
                f"Verifique se o e-mail está correto ou se a conta já foi excluída."
            )
        raise Exception(
            f"Erro ao excluir conta {email} (HTTP {erro.resp.status}): {erro}"
        )

    except Exception as erro:
        raise Exception(f"Erro inesperado ao excluir conta {email}: {erro}")


def verificar_e_deletar_conta(email: str, arquivo_id: str) -> dict:
    """
    Executa o fluxo completo de verificação e exclusão da conta.

    Primeiro verifica se o arquivo de backup existe no Drive Compartilhado.
    Somente após confirmação (status 200), prossegue com a exclusão da conta.

    Args:
        email: E-mail do colaborador cuja conta será excluída
        arquivo_id: ID do arquivo de backup no Drive (retornado pelo upload)

    Returns:
        Dicionário com informações da exclusão

    Raises:
        Exception: Se a verificação ou exclusão falhar
    """
    logger.info(f"{'─' * 40}")
    logger.info(f"Verificação e exclusão de conta: {email}")
    logger.info(f"Arquivo de backup (ID): {arquivo_id}")
    logger.info(f"{'─' * 40}")

    # Passo 1: Verificar se o backup existe no Drive Compartilhado
    verificar_arquivo_no_drive(arquivo_id)
    logger.info("Backup verificado com sucesso no Drive Compartilhado")

    # Passo 2: Excluir a conta do colaborador
    resultado = deletar_conta(email)
    logger.info(f"Conta {email} excluída após confirmação do backup")

    return resultado
