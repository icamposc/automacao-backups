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

import json
import os
import time
from pathlib import Path

import requests
from google.auth.transport.requests import AuthorizedSession
from googleapiclient.errors import HttpError

from servicos.google_auth import obter_servico_drive, _obter_credenciais
from config.configuracoes import DRIVE_PASTA_DESTINO_ID
from utils.retry import calcular_backoff
from utils.logger import obter_logger

logger = obter_logger("drive_upload")

# Número máximo de tentativas para upload
MAX_TENTATIVAS = 3


def fazer_upload(caminho_arquivo: Path, nome_arquivo: str = None, sha256: str = None) -> dict:
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
    if sha256:
        metadados["description"] = f"SHA256: {sha256}"
        metadados["appProperties"] = {"sha256": sha256}
        logger.info(f"SHA256 será salvo como metadado no Drive: {sha256[:16]}...")

    for tentativa in range(1, MAX_TENTATIVAS + 1):
        try:
            # Usa AuthorizedSession (requests) em vez de httplib2 para o upload.
            # O httplib2 falha com o Netskope porque o proxy remove o header
            # 'Location' das respostas de redirect no upload resumível.
            # O requests lida melhor com proxies corporativos neste cenário.
            ca_bundle = os.getenv("REQUESTS_CA_BUNDLE")
            credenciais = _obter_credenciais()
            sessao = AuthorizedSession(credenciais)
            if ca_bundle:
                sessao.verify = ca_bundle

            # Etapa 1: Inicia a sessão de upload resumível
            headers_inicio = {
                "X-Upload-Content-Type": "application/zip",
                "X-Upload-Content-Length": str(caminho_arquivo.stat().st_size),
                "Content-Type": "application/json; charset=UTF-8",
            }
            resposta_inicio = sessao.post(
                "https://www.googleapis.com/upload/drive/v3/files"
                f"?uploadType=resumable&supportsAllDrives=true&fields=id,name,webViewLink",
                headers=headers_inicio,
                data=json.dumps(metadados),
            )
            resposta_inicio.raise_for_status()
            url_upload = resposta_inicio.headers.get("Location")

            if not url_upload:
                raise Exception("Google Drive não retornou URL de upload resumível")

            logger.info(f"Sessão de upload iniciada. Enviando {tamanho_mb:.1f} MB...")

            # Etapa 2: Envia o arquivo em chunks de 10 MB
            tamanho_total = caminho_arquivo.stat().st_size
            chunk_size = 10 * 1024 * 1024  # 10 MB
            enviado = 0

            with open(caminho_arquivo, "rb") as arquivo:
                while enviado < tamanho_total:
                    chunk = arquivo.read(chunk_size)
                    fim = enviado + len(chunk) - 1
                    headers_chunk = {
                        "Content-Range": f"bytes {enviado}-{fim}/{tamanho_total}",
                        "Content-Type": "application/zip",
                    }
                    resp_chunk = sessao.put(url_upload, headers=headers_chunk, data=chunk)

                    if resp_chunk.status_code in (200, 201):
                        # Upload concluído
                        dados = resp_chunk.json()
                        break
                    elif resp_chunk.status_code == 308:
                        # Chunk aceito, continua
                        enviado += len(chunk)
                        progresso = int((enviado / tamanho_total) * 100)
                        logger.info(f"Progresso do upload: {progresso}%")
                    else:
                        resp_chunk.raise_for_status()

            arquivo_id = dados.get("id")
            link = dados.get("webViewLink", "Link não disponível")

            logger.info(f"Upload concluído com sucesso — Arquivo: {nome_arquivo}, ID: {arquivo_id}")
            logger.info(f"Link no Drive: {link}")

            return dados

        except Exception as erro:
            logger.error(f"Erro no upload (tentativa {tentativa}/{MAX_TENTATIVAS}): {erro}")
            if tentativa < MAX_TENTATIVAS:
                espera = calcular_backoff(tentativa)
                logger.info(f"Aguardando {espera}s antes de tentar novamente (backoff exponencial)...")
                time.sleep(espera)
            else:
                raise Exception(
                    f"Falha no upload de {nome_arquivo} "
                    f"após {MAX_TENTATIVAS} tentativas: {erro}"
                )
