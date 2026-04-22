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
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime, timedelta, timezone

from googleapiclient.errors import HttpError

from servicos.google_auth import obter_servico_vault, obter_cliente_storage
from config.configuracoes import (
    VAULT_MATTER_ID,
    POLLING_INTERVALO_SEGUNDOS,
    TIMEOUT_MAXIMO_SEGUNDOS,
    MAX_EXPORTS_SIMULTANEOS,
)
from utils.excecoes import ErroVaultTimeout
from utils.retry import calcular_backoff
from utils.logger import obter_logger

logger = obter_logger("vault_exportacao")

# Semáforo global para controlar o número de exports simultâneos
# O Google Vault permite no máximo 20 exports ao mesmo tempo por organização
_semaforo_exports = threading.Semaphore(MAX_EXPORTS_SIMULTANEOS)

# Número máximo de tentativas para operações que podem falhar
MAX_TENTATIVAS = 10

# Cache TTL para buscar_exportacao_existente: evita paginar todos os exports
# do Vault a cada chamada durante o fluxo de criação (múltiplas chamadas por backup).
_CACHE_TTL_SEGUNDOS = 300  # 5 minutos
_cache_exports: dict = {}  # f"{email}:{tipo}" → (timestamp, resultado)


def buscar_exportacao_existente(email: str, tipo: str, usar_cache: bool = True) -> dict | None:
    """
    Verifica se já existe uma exportação válida (IN_PROGRESS ou COMPLETED)
    no Vault para o e-mail e tipo informados.

    Evita criar exports duplicados quando o processo é reexecutado após
    uma falha em etapa posterior (ex: upload, compactação).

    O tipo de exportação é identificado pelo prefixo do nome:
    - "Email_{email}_" para exportações de e-mail
    - "Drive_{email}_" para exportações de Drive

    Args:
        email:       E-mail do colaborador
        tipo:        "E-MAIL" ou "DRIVE"
        usar_cache:  Se True (padrão), usa cache TTL de 5 min para evitar
                     paginação repetida da API. Passe False para forçar consulta
                     fresca (ex: verificação pós-falha de criação).

    Returns:
        Dicionário com os dados da exportação mais recente válida,
        ou None se não houver nenhuma aproveitável.
    """
    chave_cache = f"{email}:{tipo}"
    if usar_cache:
        entrada = _cache_exports.get(chave_cache)
        if entrada and (time.time() - entrada[0]) < _CACHE_TTL_SEGUNDOS:
            logger.debug(f"Cache hit: exports existentes para {email} ({tipo})")
            return entrada[1]

    prefixo = f"Email_{email}_" if tipo == "E-MAIL" else f"Drive_{email}_"
    status_validos = {"IN_PROGRESS", "COMPLETED"}

    logger.info(f"Verificando exports existentes no Vault para: {email} (tipo: {tipo})")

    try:
        servico = obter_servico_vault()
        exports = []
        page_token = None

        # Pagina todos os exports do Matter para não perder candidatos além da 1ª página
        while True:
            kwargs = {"matterId": VAULT_MATTER_ID, "pageSize": 100}
            if page_token:
                kwargs["pageToken"] = page_token
            resposta = servico.matters().exports().list(**kwargs).execute()
            exports.extend(resposta.get("exports", []))
            page_token = resposta.get("nextPageToken")
            if not page_token:
                break

        candidatos = [
            e for e in exports
            if e.get("name", "").startswith(prefixo)
            and e.get("status") in status_validos
        ]

        if not candidatos:
            logger.info(f"Nenhum export existente aproveitável para {email} (tipo: {tipo})")
            _cache_exports[chave_cache] = (time.time(), None)
            return None

        # Usa o mais recente comparando como datetime (não como string)
        def _parse_create_time(e: dict) -> datetime:
            raw = e.get("createTime", "")
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                return datetime.min.replace(tzinfo=timezone.utc)

        mais_recente = max(candidatos, key=_parse_create_time)
        logger.info(
            f"Export existente encontrado — "
            f"Nome: {mais_recente.get('name')}, "
            f"ID: {mais_recente.get('id')}, "
            f"Status: {mais_recente.get('status')}"
        )
        # Marca como reaproveitado para o orquestrador não liberar o semáforo
        mais_recente["_reaproveitado"] = True
        _cache_exports[chave_cache] = (time.time(), mais_recente)
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

    # versionDate: exporta apenas a versão mais recente de cada arquivo
    # (última versão antes de 00:00 UTC do dia seguinte), evitando a explosão
    # de artefatos causada pelo histórico de versões do Google Docs/Sheets/Slides.
    amanha = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
    logger.info(f"Export de Drive com versionDate: {amanha} (versão atual de todos os arquivos)")

    # Corpo da requisição para criar a exportação
    #
    # IMPORTANTE — owner:{email}:
    #   Filtra somente arquivos cujo PROPRIETÁRIO é o colaborador desligado.
    #   Sem este filtro, searchMethod:ACCOUNT inclui todos os arquivos que o
    #   usuário tem acesso (arquivos de outros compartilhados com ele),
    #   podendo resultar em centenas de milhares de artefatos desnecessários.
    #   Arquivos que pertencem a outros continuam existindo após a exclusão
    #   da conta — não precisam ser incluídos neste backup.
    #
    # IMPORTANTE — includeSharedDrives / includeTeamDrives:
    #   A API do Vault serializa booleanos false como ausentes na resposta,
    #   mas o campo deve ser enviado explicitamente para garantir a exclusão.
    corpo_exportacao = {
        "name": nome_export,
        "query": {
            "corpus": "DRIVE",
            "dataScope": "ALL_DATA",
            "searchMethod": "ACCOUNT",
            "terms": f"owner:{email}",
            "driveOptions": {
                "includeSharedDrives": False,
                "includeTeamDrives": False,
                "versionDate": amanha,
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
                # Última tentativa falhou — o Vault pode ter criado o export apesar do erro
                # (ex: timeout na resposta após criação bem-sucedida no servidor).
                # Verifica se o export existe antes de desistir.
                logger.warning(
                    f"Todas as tentativas falharam para {tipo} de {email}. "
                    f"Verificando se o export foi criado no Vault apesar do erro..."
                )
                exportacao_existente = buscar_exportacao_existente(email, tipo, usar_cache=False)
                if exportacao_existente:
                    logger.info(
                        f"Export {tipo} encontrado no Vault após falha de criação — "
                        f"reaproveitando: {exportacao_existente.get('id')}"
                    )
                    return exportacao_existente
                raise Exception(
                    f"Falha ao criar exportação {tipo} para {email} "
                    f"após {MAX_TENTATIVAS} tentativas: {erro}"
                )

        except Exception as erro:
            _semaforo_exports.release()
            logger.error(f"Erro inesperado ao criar exportação {tipo}: {erro}")
            raise


def liberar_semaforo_exportacao(exportacao: dict) -> None:
    """
    Libera o slot do semáforo de uma exportação que não será monitorada.

    Deve ser chamado quando a criação de uma segunda exportação falha após a
    primeira ter sido criada com sucesso — nesse caso, monitorar_exportacao
    nunca é chamada para a primeira e o slot ficaria retido indefinidamente.

    Não tem efeito se a exportação foi reaproveitada (buscar_exportacao_existente),
    pois exports reaproveitados não adquirem o semáforo.

    Args:
        exportacao: Dicionário retornado por criar_exportacao_email ou
                    criar_exportacao_drive
    """
    if exportacao.get("_reaproveitado", False):
        return  # Export reaproveitado não adquiriu semáforo — nada a liberar
    _semaforo_exports.release()
    logger.debug(f"Semáforo liberado manualmente para export {exportacao.get('id')}")


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
    ultima_exportacao = None

    try:
        while True:
            # Verifica se ultrapassou o timeout máximo
            tempo_decorrido = time.time() - tempo_inicio
            if tempo_decorrido > TIMEOUT_MAXIMO_SEGUNDOS:
                stats = ultima_exportacao.get("stats", {}) if ultima_exportacao else {}
                raise ErroVaultTimeout(
                    f"Timeout: Export {export_id} não completou em "
                    f"{TIMEOUT_MAXIMO_SEGUNDOS / 3600:.1f} horas",
                    stats=stats,
                )

            # Consulta o status atual da exportação
            exportacao = (
                servico.matters()
                .exports()
                .get(matterId=VAULT_MATTER_ID, exportId=export_id)
                .execute()
            )
            ultima_exportacao = exportacao

            status = exportacao.get("status")
            nome = exportacao.get("name", "")
            minutos = tempo_decorrido / 60

            # Extrai progresso de artefatos retornado pela API do Vault
            stats = exportacao.get("stats", {})
            artefatos_exportados = int(stats.get("exportedArtifactCount") or 0)
            artefatos_total = int(stats.get("totalArtifactCount") or 0)
            tamanho_mb = int(stats.get("sizeInBytes") or 0) / (1024 * 1024)

            if artefatos_total > 0:
                progresso_pct = artefatos_exportados / artefatos_total * 100
                logger.info(
                    f"Export '{nome}' (ID: {export_id}) — "
                    f"Status: {status} — Tempo: {minutos:.1f} min — "
                    f"Artefatos: {artefatos_exportados:,}/{artefatos_total:,} "
                    f"({progresso_pct:.1f}%) — Tamanho: {tamanho_mb:.0f} MB"
                )
            else:
                logger.info(
                    f"Export '{nome}' (ID: {export_id}) — "
                    f"Status: {status} — Tempo: {minutos:.1f} min"
                )

            # Export concluído com sucesso
            if status == "COMPLETED":
                logger.info(
                    f"Export {export_id} COMPLETADO em {minutos:.1f} min — "
                    f"Total: {artefatos_total:,} artefatos, {tamanho_mb:.0f} MB"
                )
                return exportacao

            # Export falhou
            if status == "FAILED":
                raise Exception(f"Export {export_id} FALHOU. Detalhes: {exportacao}")

            # Backoff adaptativo: aumenta o intervalo conforme o tempo passa,
            # pois exports longos (Drive com muitos arquivos) demoram horas.
            # Caps em 10× o intervalo base para não ficar polling demais nem de menos.
            fator = min(10, 1 + int(tempo_decorrido / 3600))  # +1 fator por hora decorrida
            intervalo = POLLING_INTERVALO_SEGUNDOS * fator
            logger.debug(
                f"Export {export_id} em andamento. "
                f"Próxima verificação em {intervalo}s "
                f"(fator adaptativo: {fator}×)..."
            )
            time.sleep(intervalo)

    finally:
        # Libera o semáforo apenas se ele foi adquirido por este processo.
        # Exports reaproveitados (buscar_exportacao_existente) não adquirem semáforo.
        if semaforo_adquirido:
            _semaforo_exports.release()
            logger.debug(f"Semáforo liberado para export {export_id}")


def baixar_exportacao(
    exportacao: dict,
    pasta_destino: Path,
    on_progresso: callable = None,
) -> list[Path]:
    """
    Baixa os arquivos de uma exportação concluída do Cloud Storage.

    O Google Vault armazena os arquivos exportados em buckets do
    Cloud Storage. Esta função encontra os arquivos e os baixa
    para a pasta de destino local em chunks, reportando progresso
    por bytes (não por arquivo) para atualização granular da interface.

    Args:
        exportacao:    Dicionário com dados da exportação (retornado por monitorar_exportacao)
        pasta_destino: Pasta local onde os arquivos serão salvos
        on_progresso:  Callback opcional chamado a cada chunk baixado com o
                       percentual de conclusão em bytes (0–100). Nunca
                       lança exceção — erros são silenciados para não interromper o download.

    Returns:
        Lista de caminhos (Path) dos arquivos baixados

    Raises:
        Exception: Se falhar ao baixar os arquivos
    """
    _TAMANHO_CHUNK = 10 * 1024 * 1024  # 10 MB por chunk

    export_id = exportacao.get("id")
    nome = exportacao.get("name", "desconhecido")
    logger.info(f"Iniciando download do export '{nome}' (ID: {export_id})")

    cloud_sink = exportacao.get("cloudStorageSink", {})
    arquivos_info = cloud_sink.get("files", [])

    if not arquivos_info:
        logger.warning(f"Export '{nome}' não contém arquivos para download")
        return []

    logger.info(f"Export '{nome}' contém {len(arquivos_info)} arquivo(s) para download")

    pasta_destino.mkdir(parents=True, exist_ok=True)
    cliente_storage = obter_cliente_storage()

    # ── Pré-carrega tamanhos dos blobs em paralelo para calcular progresso por bytes ──
    def _obter_tamanho(info: dict) -> int:
        try:
            blob = cliente_storage.bucket(info["bucketName"]).blob(info["objectName"])
            blob.reload(timeout=60)
            return blob.size or 0
        except Exception as e:
            logger.warning(f"Não foi possível obter tamanho de {info.get('objectName')}: {e}")
            return 0

    with ThreadPoolExecutor(max_workers=5) as ex:
        tamanhos = list(ex.map(_obter_tamanho, arquivos_info))

    tamanho_total = sum(tamanhos)
    for info, tam in zip(arquivos_info, tamanhos):
        info["_tamanho"] = tam

    logger.info(
        f"Export '{nome}' — tamanho total: {tamanho_total / (1024**3):.2f} GB "
        f"em {len(arquivos_info)} arquivo(s)"
    )

    # ── Contador compartilhado de bytes baixados (thread-safe) ──
    bytes_baixados = 0
    lock = threading.Lock()
    arquivos_baixados: list[Path] = []

    def _on_chunk(n_bytes: int) -> None:
        """Atualiza contador global e dispara callback de progresso."""
        nonlocal bytes_baixados
        if not on_progresso or tamanho_total == 0:
            return
        with lock:
            bytes_baixados = max(0, bytes_baixados + n_bytes)
            pct = min(100, int(bytes_baixados / tamanho_total * 100))
        try:
            on_progresso(pct)
        except Exception:
            pass

    def _baixar_arquivo(info_arquivo: dict) -> Path | None:
        """Baixa um único arquivo por chunks com retry e progresso por bytes."""
        nome_bucket = info_arquivo.get("bucketName")
        nome_objeto = info_arquivo.get("objectName")

        if not nome_bucket or not nome_objeto:
            logger.warning(f"Arquivo sem bucket ou objeto: {info_arquivo}")
            return None

        nome_arquivo_local = nome_objeto.split("/")[-1]
        caminho_local = pasta_destino / nome_arquivo_local
        logger.info(f"Baixando: gs://{nome_bucket}/{nome_objeto} → {caminho_local}")

        for tentativa in range(1, MAX_TENTATIVAS + 1):
            bytes_tentativa = 0
            try:
                bucket = cliente_storage.bucket(nome_bucket)
                blob = bucket.blob(nome_objeto)

                with open(str(caminho_local), "wb") as f:
                    with blob.open("rb", chunk_size=_TAMANHO_CHUNK, timeout=300) as source:
                        while True:
                            chunk = source.read(_TAMANHO_CHUNK)
                            if not chunk:
                                break
                            f.write(chunk)
                            bytes_tentativa += len(chunk)
                            _on_chunk(len(chunk))

                tamanho_mb = caminho_local.stat().st_size / (1024 * 1024)
                logger.info(f"Download concluído: {nome_arquivo_local} ({tamanho_mb:.1f} MB)")
                return caminho_local

            except Exception as erro:
                # Desconta bytes desta tentativa falha para não distorcer o progresso
                if bytes_tentativa:
                    _on_chunk(-bytes_tentativa)

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

    # Paraleliza o download com até 6 threads simultâneas
    with ThreadPoolExecutor(max_workers=6) as executor:
        futuros = {executor.submit(_baixar_arquivo, info): info for info in arquivos_info}
        for futuro in as_completed(futuros):
            resultado = futuro.result()
            if resultado:
                with lock:
                    arquivos_baixados.append(resultado)
                logger.info(
                    f"Arquivos concluídos: {len(arquivos_baixados)}/{len(arquivos_info)} "
                    f"— {bytes_baixados / (1024**3):.2f} GB baixados"
                )

    logger.info(
        f"Download do export '{nome}' finalizado — "
        f"{len(arquivos_baixados)} arquivo(s) baixados"
    )
    return arquivos_baixados
