"""
============================================================
Módulo de Compactação — Automação de Backups
============================================================
Versão: 1.1.0
Data:   2026-05-11
Descrição: Compacta arquivos exportados do Vault em um ZIP
           único + calcula SHA256 para verificação no Drive.

Decisões de design:
- ZIP_STORED (sem compressão). Os exports do Vault são PST
  e ZIP que já vêm comprimidos do Google — DEFLATE só queima
  CPU (até 100%) por horas com taxa de redução próxima de 0%.
- allowZip64=True. Sem isso, ZipFile lança RuntimeError em
  arquivos individuais > 4 GB (frequente em Drive de
  colaborador).
- SHA256 em passe único sobre o ZIP final (necessário para
  validar a integridade do central directory record escrito
  no close()). A re-leitura é sequencial e cache-friendly
  quando feita imediatamente após a criação.
============================================================
Histórico:
  1.1.0 (2026-05-11) — ZIP_STORED + allowZip64 + on_progresso
                        (resolve travamentos por CPU 100% em
                        compactação de PSTs grandes — P2.2/P2.4
                        de Pendencias.MD)
  1.0.0 (2026-02-19) — Versão inicial (ZIP_DEFLATED)
============================================================
"""

import hashlib
import time
import zipfile
import shutil
from pathlib import Path

from utils.logger import obter_logger

logger = obter_logger("compactacao")

# Throttle do callback de progresso: nunca chama mais de uma vez por
# 30s OU por mudança menor que 1%. Sem isso, em ZIPs com muitos
# arquivos pequenos, on_progresso seria invocado milhares de vezes.
_INTERVALO_MIN_S = 30.0
_DELTA_MIN_PCT = 1


def compactar_arquivos(
    pasta_origem: Path,
    caminho_zip: Path,
    on_progresso: callable = None,
) -> tuple[Path, str]:
    """
    Compacta todos os arquivos da pasta_origem (recursivo) em caminho_zip
    sem compressão, e retorna o caminho + SHA256 do ZIP gerado.

    Args:
        pasta_origem: Pasta com os arquivos a compactar.
        caminho_zip:  Caminho de saída do ZIP.
        on_progresso: Callback opcional `(pct: int) -> None` invocado com
                      o percentual de bytes escritos (0–100). Falhas no
                      callback são silenciadas para não interromper a
                      compactação.

    Returns:
        Tupla `(caminho_zip, sha256_hex)`.

    Raises:
        Exception: Se a pasta não existir, estiver vazia, ou não houver
                   espaço em disco suficiente.
    """
    logger.info(f"Iniciando compactação de: {pasta_origem}")
    logger.info(f"Arquivo ZIP de destino: {caminho_zip}")

    if not pasta_origem.exists():
        raise Exception(f"Pasta de origem não encontrada: {pasta_origem}")

    arquivos = []
    tamanho_total = 0
    for entrada in pasta_origem.rglob("*"):
        if entrada.is_file():
            arquivos.append(entrada)
            tamanho_total += entrada.stat().st_size

    if not arquivos:
        raise Exception(f"Pasta de origem está vazia: {pasta_origem}")

    tamanho_total_mb = tamanho_total / (1024 * 1024)
    logger.info(f"Encontrados {len(arquivos)} arquivo(s) para compactar — {tamanho_total_mb:.1f} MB")

    # Para ZIP_STORED, o ZIP final ≈ tamanho_total + overhead de headers.
    # Margem de 10% cobre central directory + local file headers.
    espaco_livre = shutil.disk_usage(caminho_zip.parent).free
    margem_necessaria = int(tamanho_total * 1.10)
    if espaco_livre < margem_necessaria:
        raise Exception(
            f"Espaço em disco insuficiente! "
            f"Necessário: {margem_necessaria / (1024 * 1024):.1f} MB (com 10% de margem), "
            f"Disponível: {espaco_livre / (1024 * 1024):.1f} MB"
        )

    caminho_zip.parent.mkdir(parents=True, exist_ok=True)

    _ultimo_emit = {"pct": -1, "ts": 0.0}

    def _emit(bytes_escritos: int) -> None:
        if not on_progresso or tamanho_total == 0:
            return
        pct = int(min(100, bytes_escritos / tamanho_total * 100))
        agora = time.monotonic()
        if (pct - _ultimo_emit["pct"]) < _DELTA_MIN_PCT and (agora - _ultimo_emit["ts"]) < _INTERVALO_MIN_S:
            return
        _ultimo_emit["pct"] = pct
        _ultimo_emit["ts"] = agora
        try:
            on_progresso(pct)
        except Exception:
            pass  # progresso é informativo — nunca interrompe

    bytes_escritos = 0
    with zipfile.ZipFile(
        caminho_zip,
        "w",
        compression=zipfile.ZIP_STORED,
        allowZip64=True,
    ) as zip_file:
        for i, arquivo in enumerate(arquivos, 1):
            nome_no_zip = arquivo.relative_to(pasta_origem)
            zip_file.write(arquivo, nome_no_zip)
            bytes_escritos += arquivo.stat().st_size
            _emit(bytes_escritos)
            if i % 500 == 0 or i == len(arquivos):
                logger.debug(f"Compactando... {i}/{len(arquivos)} arquivo(s)")

    # Garante chamada final em 100% (passa pelo throttle no pct, mas força no fim)
    if on_progresso and tamanho_total > 0:
        try:
            on_progresso(100)
        except Exception:
            pass

    tamanho_zip = caminho_zip.stat().st_size
    tamanho_zip_mb = tamanho_zip / (1024 * 1024)
    logger.info(
        f"Compactação concluída — ZIP: {tamanho_zip_mb:.1f} MB "
        f"(ZIP_STORED, sem compressão)"
    )

    sha256_hex = calcular_sha256(caminho_zip)
    return caminho_zip, sha256_hex


def calcular_sha256(caminho: Path) -> str:
    """
    Calcula SHA256 de um arquivo em blocos de 8 MB (não carrega tudo na
    memória — necessário para ZIPs de dezenas/centenas de GB).
    """
    sha256 = hashlib.sha256()
    bloco_size = 8 * 1024 * 1024

    logger.info(f"Calculando SHA256 de: {caminho.name}")
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(bloco_size), b""):
            sha256.update(bloco)

    digest = sha256.hexdigest()
    logger.info(f"SHA256 calculado: {digest[:16]}... ({caminho.name})")
    return digest
