"""
============================================================
Módulo de Exportação do Google Vault — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-02-19
Descrição: Módulo CRÍTICO que gerencia todo o ciclo de vida
           das exportações no Google Vault:
           1. Criar exportações (e-mail e Drive)
           2. Monitorar status até COMPLETED
           3. Baixar arquivos exportados do Cloud Storage

           Inclui controle de concorrência (semáforo) para
           respeitar o limite de 20 exports simultâneos
           do Google Vault.
============================================================
Histórico:
  1.0.0 (2026-02-19) — Versão inicial
============================================================
"""

import time
import threading
from pathlib import Path
from datetime import datetime

from googleapiclient.errors import HttpError

from servicos.google_auth import obter_servico_vault, obter_cliente_storage
from config.configuracoes import (
    VAULT_MATTER_ID,
    POLLING_INTERVALO_SEGUNDOS,
    TIMEOUT_MAXIMO_SEGUNDOS,
    MAX_EXPORTS_SIMULTANEOS,
    PASTA_TEMP,
)
from utils.retry import calcular_backoff
from utils.logger import obter_logger

logger = obter_logger("vault_exportacao")

# Semáforo global para controlar o número de exports simultâneos
# O Google Vault permite no máximo 20 exports ao mesmo tempo por organização
_semaforo_exports = threading.Semaphore(MAX_EXPORTS_SIMULTANEOS)

# Número máximo de tentativas para operações que podem falhar
MAX_TENTATIVAS = 3


def buscar_exportacao_existente(email: str, tipo: str) -> dict | None:
    """
    Verifica se já existe uma exportação válida (IN_PROGRESS ou COMPLETED)
    no Vault para o e-mail e tipo informados.

    Evita criar exports duplicados quando o processo é reexecutado após
    uma falha em etapa posterior (ex: upload, compactação).

    O tipo de exportação é identificado pelo prefixo do nome:
    - "Email_{email}_" para exportações de e-mail
    - "Drive_{email}_" para exportações de Drive

    Args:
        email: E-mail do colaborador
        tipo: "E-MAIL" ou "DRIVE"

    Returns:
        Dicionário com os dados da exportação mais recente válida,
        ou None se não houver nenhuma aproveitável.
    """
    prefixo = f"Email_{email}_" if tipo == "E-MAIL" else f"Drive_{email}_"
    status_validos = {"IN_PROGRESS", "COMPLETED"}

    logger.info(f"Verificando exports existentes no Vault para: {email} (tipo: {tipo})")

    try:
        servico = obter_servico_vault()
        resposta = (
            servico.matters()
            .exports()
            .list(matterId=VAULT_MATTER_ID, pageSize=50)
            .execute()
        )

        exports = resposta.get("exports", [])
        candidatos = [
            e for e in exports
            if e.get("name", "").startswith(prefixo)
            and e.get("status") in status_validos
        ]

        if not candidatos:
            logger.info(f"Nenhum export existente aproveitável para {email} (tipo: {tipo})")
            return None

        # Usa o mais recente (maior createTime)
        mais_recente = max(candidatos, key=lambda e: e.get("createTime", ""))
        logger.info(
            f"Export existente encontrado — "
            f"Nome: {mais_recente.get('name')}, "
            f"ID: {mais_recente.get('id')}, "
            f"Status: {mais_recente.get('status')}"
        )
        # Marca como reaproveitado para o orquestrador não liberar o semáforo
        mais_recente["_reaproveitado"] = True
        return mais_recente

    except Exception as erro:
        logger.warning(f"Erro ao buscar exports existentes: {erro} — criando novo export")
        return None


def criar_exportacao_email(email: str) -> dict:
    """
    Cria uma exportação de e-mails (Gmail) no Google Vault para o
    colaborador especificado.

    A exportação é criada no Matter "BkpE-mails" e inclui TODOS os
    e-mails da conta, no formato PST.

    Args:
        email: Endereço de e-mail do colaborador desligado

    Returns:
        Dicionário com os dados da exportação criada (inclui 'id' e 'status')

    Raises:
        Exception: Se falhar após todas as tentativas de retry
    """
    logger.info(f"Criando exportação de E-MAIL para: {email}")

    # Verifica se já existe um export válido antes de criar um novo
    existente = buscar_exportacao_existente(email, "E-MAIL")
    if existente:
        logger.info(f"Reaproveitando export de E-MAIL existente: {existente.get('id')}")
        return existente

    # Timestamp para nome único da exportação
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    nome_export = f"Email_{email}_{timestamp}"

    # Corpo da requisição para criar a exportação
    corpo_exportacao = {
        "name": nome_export,
        "query": {
            "corpus": "MAIL",
            "dataScope": "ALL_DATA",
            "searchMethod": "ACCOUNT",
            "mailOptions": {
                "excludeDrafts": False,
            },
            "accountInfo": {
                "emails": [email],
            },
        },
        "exportOptions": {
            "mailOptions": {
                "exportFormat": "PST",
                "showConfidentialModeContent": True,
            },
        },
    }

    return _criar_exportacao_com_retry(corpo_exportacao, email, "E-MAIL")


def criar_exportacao_drive(email: str) -> dict:
    """
    Cria uma exportação de arquivos do Google Drive no Google Vault
    para o colaborador especificado.

    A exportação é criada no Matter "BkpE-mails" e inclui TODOS os
    arquivos do Drive do usuário.

    Args:
        email: Endereço de e-mail do colaborador desligado

    Returns:
        Dicionário com os dados da exportação criada (inclui 'id' e 'status')

    Raises:
        Exception: Se falhar após todas as tentativas de retry
    """
    logger.info(f"Criando exportação de DRIVE para: {email}")

    # Verifica se já existe um export válido antes de criar um novo
    existente = buscar_exportacao_existente(email, "DRIVE")
    if existente:
        logger.info(f"Reaproveitando export de DRIVE existente: {existente.get('id')}")
        return existente

    # Timestamp para nome único da exportação
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    nome_export = f"Drive_{email}_{timestamp}"

    # Corpo da requisição para criar a exportação
    corpo_exportacao = {
        "name": nome_export,
        "query": {
            "corpus": "DRIVE",
            "dataScope": "ALL_DATA",
            "searchMethod": "ACCOUNT",
            "driveOptions": {
                "includeSharedDrives": False,
                "includeTeamDrives": False,
            },
            "accountInfo": {
                "emails": [email],
            },
        },
        "exportOptions": {
            "driveOptions": {
                "includeAccessInfo": False,
            },
        },
    }

    return _criar_exportacao_com_retry(corpo_exportacao, email, "DRIVE")


def _criar_exportacao_com_retry(corpo: dict, email: str, tipo: str) -> dict:
    """
    Tenta criar uma exportação no Vault com retry automático.

    Aguarda o semáforo antes de criar (controle de concorrência)
    e tenta até MAX_TENTATIVAS vezes em caso de falha.

    Args:
        corpo: Corpo da requisição para a API do Vault
        email: E-mail do colaborador (para logs)
        tipo: Tipo da exportação ("E-MAIL" ou "DRIVE", para logs)

    Returns:
        Dados da exportação criada

    Raises:
        Exception: Se falhar após todas as tentativas
    """
    for tentativa in range(1, MAX_TENTATIVAS + 1):
        try:
            # Aguarda uma vaga no semáforo (respeita limite de exports simultâneos)
            logger.debug(f"Aguardando vaga no semáforo para export {tipo} de {email}...")
            _semaforo_exports.acquire()
            logger.debug(f"Vaga obtida no semáforo para export {tipo} de {email}")

            # Cria o serviço do Vault e faz a requisição
            servico = obter_servico_vault()
            exportacao = (
                servico.matters()
                .exports()
                .create(matterId=VAULT_MATTER_ID, body=corpo)
                .execute()
            )

            export_id = exportacao.get("id")
            logger.info(
                f"Exportação {tipo} criada com sucesso — "
                f"E-mail: {email}, Export ID: {export_id}"
            )
            return exportacao

        except HttpError as erro:
            _semaforo_exports.release()
            logger.error(
                f"Erro ao criar exportação {tipo} para {email} "
                f"(tentativa {tentativa}/{MAX_TENTATIVAS}): {erro}"
            )
            if tentativa < MAX_TENTATIVAS:
                espera = calcular_backoff(tentativa)
                logger.info(f"Aguardando {espera}s antes de tentar novamente (backoff exponencial)...")
                time.sleep(espera)
            else:
                raise Exception(
                    f"Falha ao criar exportação {tipo} para {email} "
                    f"após {MAX_TENTATIVAS} tentativas: {erro}"
                )

        except Exception as erro:
            _semaforo_exports.release()
            logger.error(f"Erro inesperado ao criar exportação {tipo}: {erro}")
            raise


def monitorar_exportacao(export_id: str, semaforo_adquirido: bool = True) -> dict:
    """
    Monitora o status de uma exportação no Vault até que ela seja
    concluída (COMPLETED) ou falhe (FAILED).

    Faz polling a cada POLLING_INTERVALO_SEGUNDOS e respeita o
    TIMEOUT_MAXIMO_SEGUNDOS para evitar espera infinita.

    IMPORTANTE: Libera o semáforo quando a exportação termina,
    permitindo que novas exportações sejam criadas.

    Args:
        export_id: ID da exportação no Vault

    Returns:
        Dados completos da exportação finalizada

    Raises:
        Exception: Se o export falhar ou ultrapassar o timeout
    """
    logger.info(f"Iniciando monitoramento do export: {export_id}")

    servico = obter_servico_vault()
    tempo_inicio = time.time()

    try:
        while True:
            # Verifica se ultrapassou o timeout máximo
            tempo_decorrido = time.time() - tempo_inicio
            if tempo_decorrido > TIMEOUT_MAXIMO_SEGUNDOS:
                raise Exception(
                    f"Timeout: Export {export_id} não completou em "
                    f"{TIMEOUT_MAXIMO_SEGUNDOS / 3600:.1f} horas"
                )

            # Consulta o status atual da exportação
            exportacao = (
                servico.matters()
                .exports()
                .get(matterId=VAULT_MATTER_ID, exportId=export_id)
                .execute()
            )

            status = exportacao.get("status")
            nome = exportacao.get("name", "")
            minutos = tempo_decorrido / 60

            logger.info(
                f"Export '{nome}' (ID: {export_id}) — "
                f"Status: {status} — Tempo: {minutos:.1f} min"
            )

            # Export concluído com sucesso
            if status == "COMPLETED":
                logger.info(f"Export {export_id} COMPLETADO com sucesso em {minutos:.1f} min")
                return exportacao

            # Export falhou
            if status == "FAILED":
                raise Exception(f"Export {export_id} FALHOU. Detalhes: {exportacao}")

            # Ainda em andamento — aguarda antes de verificar novamente
            logger.debug(
                f"Export {export_id} em andamento. "
                f"Próxima verificação em {POLLING_INTERVALO_SEGUNDOS}s..."
            )
            time.sleep(POLLING_INTERVALO_SEGUNDOS)

    finally:
        # Libera o semáforo apenas se ele foi adquirido por este processo.
        # Exports reaproveitados (buscar_exportacao_existente) não adquirem semáforo.
        if semaforo_adquirido:
            _semaforo_exports.release()
            logger.debug(f"Semáforo liberado para export {export_id}")


def baixar_exportacao(exportacao: dict, pasta_destino: Path) -> list[Path]:
    """
    Baixa os arquivos de uma exportação concluída do Cloud Storage.

    O Google Vault armazena os arquivos exportados em buckets do
    Cloud Storage. Esta função encontra os arquivos e os baixa
    para a pasta de destino local.

    Args:
        exportacao: Dicionário com dados da exportação (retornado por monitorar_exportacao)
        pasta_destino: Pasta local onde os arquivos serão salvos

    Returns:
        Lista de caminhos (Path) dos arquivos baixados

    Raises:
        Exception: Se falhar ao baixar os arquivos
    """
    export_id = exportacao.get("id")
    nome = exportacao.get("name", "desconhecido")
    logger.info(f"Iniciando download do export '{nome}' (ID: {export_id})")

    # Obtém informações sobre os arquivos exportados
    # O campo 'cloudStorageSink' contém os detalhes do bucket e arquivos
    cloud_sink = exportacao.get("cloudStorageSink", {})
    arquivos_info = cloud_sink.get("files", [])

    if not arquivos_info:
        logger.warning(f"Export '{nome}' não contém arquivos para download")
        return []

    logger.info(f"Export '{nome}' contém {len(arquivos_info)} arquivo(s) para download")

    # Garante que a pasta de destino existe
    pasta_destino.mkdir(parents=True, exist_ok=True)

    # Cria o cliente do Cloud Storage
    cliente_storage = obter_cliente_storage()

    arquivos_baixados = []

    for info_arquivo in arquivos_info:
        # Cada arquivo tem bucketName e objectName
        nome_bucket = info_arquivo.get("bucketName")
        nome_objeto = info_arquivo.get("objectName")

        if not nome_bucket or not nome_objeto:
            logger.warning(f"Arquivo sem bucket ou objeto: {info_arquivo}")
            continue

        # Nome do arquivo local (usa apenas o nome final do caminho do objeto)
        nome_arquivo_local = nome_objeto.split("/")[-1]
        caminho_local = pasta_destino / nome_arquivo_local

        logger.info(f"Baixando: gs://{nome_bucket}/{nome_objeto} → {caminho_local}")

        # Tenta baixar com retry
        for tentativa in range(1, MAX_TENTATIVAS + 1):
            try:
                bucket = cliente_storage.bucket(nome_bucket)
                blob = bucket.blob(nome_objeto)
                blob.download_to_filename(str(caminho_local))

                tamanho_mb = caminho_local.stat().st_size / (1024 * 1024)
                logger.info(
                    f"Download concluído: {nome_arquivo_local} ({tamanho_mb:.1f} MB)"
                )
                arquivos_baixados.append(caminho_local)
                break

            except Exception as erro:
                logger.error(
                    f"Erro ao baixar {nome_arquivo_local} "
                    f"(tentativa {tentativa}/{MAX_TENTATIVAS}): {erro}"
                )
                if tentativa < MAX_TENTATIVAS:
                    espera = calcular_backoff(tentativa)
                    logger.info(f"Aguardando {espera}s antes de tentar novamente (backoff exponencial)...")
                    time.sleep(espera)
                else:
                    raise Exception(
                        f"Falha ao baixar {nome_arquivo_local} "
                        f"após {MAX_TENTATIVAS} tentativas: {erro}"
                    )

    logger.info(
        f"Download do export '{nome}' finalizado — "
        f"{len(arquivos_baixados)} arquivo(s) baixados"
    )
    return arquivos_baixados
