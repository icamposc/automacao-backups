"""
============================================================
Módulo de Upload para Google Drive — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-02-19
Descrição: Faz upload de arquivos .zip para o Google Drive
           Compartilhado (Shared Drive). Utiliza upload
           resumível para lidar com arquivos grandes e
           conexões instáveis.
============================================================
Histórico:
  1.0.0 (2026-02-19) — Versão inicial
============================================================
"""

import time
from pathlib import Path

from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

from servicos.google_auth import obter_servico_drive
from config.configuracoes import DRIVE_PASTA_DESTINO_ID
from utils.logger import obter_logger

logger = obter_logger("drive_upload")

# Número máximo de tentativas para upload
MAX_TENTATIVAS = 3

# Intervalo entre tentativas em caso de falha (segundos)
INTERVALO_RETRY = 30


def fazer_upload(caminho_arquivo: Path, nome_arquivo: str = None) -> dict:
    """
    Faz upload de um arquivo para o Google Drive Compartilhado.

    Utiliza upload resumível (resumable), que é obrigatório para
    arquivos grandes e permite retomar o upload em caso de
    interrupção na conexão.

    O parâmetro supportsAllDrives=True é OBRIGATÓRIO para
    fazer upload para Shared Drives (Drive Compartilhado).

    Args:
        caminho_arquivo: Caminho local do arquivo a ser enviado
        nome_arquivo: Nome do arquivo no Drive (opcional, usa o nome original se não fornecido)

    Returns:
        Dicionário com dados do arquivo criado no Drive, incluindo:
        - 'id': ID do arquivo no Drive
        - 'name': Nome do arquivo
        - 'webViewLink': Link para visualizar o arquivo no Drive

    Raises:
        Exception: Se falhar após todas as tentativas de retry
    """
    if not caminho_arquivo.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {caminho_arquivo}")

    # Usa o nome original do arquivo se nenhum nome foi especificado
    if not nome_arquivo:
        nome_arquivo = caminho_arquivo.name

    tamanho_mb = caminho_arquivo.stat().st_size / (1024 * 1024)
    logger.info(f"Iniciando upload para Google Drive: {nome_arquivo} ({tamanho_mb:.1f} MB)")

    # Metadados do arquivo no Drive
    metadados = {
        "name": nome_arquivo,
        "parents": [DRIVE_PASTA_DESTINO_ID],
    }

    # Prepara o upload resumível
    # chunksize=-1 faz upload em um único chunk (mais eficiente para arquivos < 5 GB)
    # Para arquivos muito grandes, pode-se ajustar para chunks menores
    media = MediaFileUpload(
        str(caminho_arquivo),
        mimetype="application/zip",
        resumable=True,
    )

    for tentativa in range(1, MAX_TENTATIVAS + 1):
        try:
            servico = obter_servico_drive()

            # Cria o arquivo no Drive Compartilhado
            # supportsAllDrives=True é OBRIGATÓRIO para Shared Drives
            requisicao = servico.files().create(
                body=metadados,
                media_body=media,
                fields="id, name, webViewLink",
                supportsAllDrives=True,
            )

            # Executa o upload com acompanhamento de progresso
            resposta = None
            while resposta is None:
                status_upload, resposta = requisicao.next_chunk()
                if status_upload:
                    progresso = int(status_upload.progress() * 100)
                    logger.info(f"Progresso do upload: {progresso}%")

            # Upload concluído com sucesso
            arquivo_id = resposta.get("id")
            link = resposta.get("webViewLink", "Link não disponível")

            logger.info(
                f"Upload concluído com sucesso — "
                f"Arquivo: {nome_arquivo}, ID: {arquivo_id}"
            )
            logger.info(f"Link no Drive: {link}")

            return resposta

        except HttpError as erro:
            logger.error(
                f"Erro no upload (tentativa {tentativa}/{MAX_TENTATIVAS}): {erro}"
            )
            if tentativa < MAX_TENTATIVAS:
                logger.info(f"Aguardando {INTERVALO_RETRY}s antes de tentar novamente...")
                time.sleep(INTERVALO_RETRY)
            else:
                raise Exception(
                    f"Falha no upload de {nome_arquivo} "
                    f"após {MAX_TENTATIVAS} tentativas: {erro}"
                )

        except Exception as erro:
            logger.error(f"Erro inesperado no upload: {erro}")
            raise
