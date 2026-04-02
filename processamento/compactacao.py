"""
============================================================
Módulo de Compactação — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-02-19
Descrição: Compacta os arquivos exportados do Vault em um
           único arquivo .zip para facilitar o armazenamento
           e transferência para o Google Drive.
============================================================
Histórico:
  1.0.0 (2026-02-19) — Versão inicial
============================================================
"""

import hashlib
import zipfile
import shutil
from pathlib import Path

from utils.logger import obter_logger

logger = obter_logger("compactacao")


def compactar_arquivos(pasta_origem: Path, caminho_zip: Path) -> Path:
    """
    Compacta todos os arquivos de uma pasta em um único arquivo .zip.

    Utiliza compressão ZIP_DEFLATED para reduzir o tamanho dos arquivos.
    Todos os arquivos encontrados na pasta de origem (incluindo subpastas)
    são adicionados ao ZIP.

    Args:
        pasta_origem: Pasta contendo os arquivos a serem compactados
        caminho_zip: Caminho completo para o arquivo .zip de saída

    Returns:
        Caminho do arquivo .zip criado

    Raises:
        Exception: Se a pasta de origem não existir ou estiver vazia,
                   ou se não houver espaço em disco suficiente
    """
    logger.info(f"Iniciando compactação de: {pasta_origem}")
    logger.info(f"Arquivo ZIP de destino: {caminho_zip}")

    # Verifica se a pasta de origem existe
    if not pasta_origem.exists():
        raise Exception(f"Pasta de origem não encontrada: {pasta_origem}")

    # Lista todos os arquivos na pasta (recursivamente)
    arquivos = list(pasta_origem.rglob("*"))
    arquivos = [a for a in arquivos if a.is_file()]

    if not arquivos:
        raise Exception(f"Pasta de origem está vazia: {pasta_origem}")

    logger.info(f"Encontrados {len(arquivos)} arquivo(s) para compactar")

    # Calcula o tamanho total dos arquivos originais
    tamanho_total = sum(a.stat().st_size for a in arquivos)
    tamanho_total_mb = tamanho_total / (1024 * 1024)
    logger.info(f"Tamanho total dos arquivos: {tamanho_total_mb:.1f} MB")

    # Verifica espaço em disco disponível
    espaco_livre = shutil.disk_usage(caminho_zip.parent).free
    espaco_livre_mb = espaco_livre / (1024 * 1024)

    # Precisa de pelo menos o tamanho dos arquivos originais de espaço livre
    # (o ZIP provavelmente será menor, mas é uma margem de segurança)
    if espaco_livre < tamanho_total:
        raise Exception(
            f"Espaço em disco insuficiente! "
            f"Necessário: {tamanho_total_mb:.1f} MB, "
            f"Disponível: {espaco_livre_mb:.1f} MB"
        )

    # Garante que o diretório de destino do ZIP existe
    caminho_zip.parent.mkdir(parents=True, exist_ok=True)

    # Cria o arquivo ZIP com compressão
    with zipfile.ZipFile(caminho_zip, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for arquivo in arquivos:
            # Nome relativo dentro do ZIP (preserva estrutura de pastas)
            nome_no_zip = arquivo.relative_to(pasta_origem)
            logger.debug(f"Adicionando ao ZIP: {nome_no_zip}")
            zip_file.write(arquivo, nome_no_zip)

    # Confirma o tamanho do ZIP criado
    tamanho_zip = caminho_zip.stat().st_size
    tamanho_zip_mb = tamanho_zip / (1024 * 1024)
    taxa_compressao = (1 - tamanho_zip / tamanho_total) * 100 if tamanho_total > 0 else 0

    logger.info(
        f"Compactação concluída — ZIP: {tamanho_zip_mb:.1f} MB "
        f"(redução de {taxa_compressao:.1f}%)"
    )

    return caminho_zip


def calcular_sha256(caminho: Path) -> str:
    """
    Calcula o hash SHA256 de um arquivo lendo em blocos de 8 MB.

    Leitura em blocos é necessária para arquivos grandes (dezenas de GB)
    para não carregar o arquivo inteiro na memória.

    Args:
        caminho: Caminho do arquivo a ser verificado

    Returns:
        Hash SHA256 em formato hexadecimal (64 caracteres)
    """
    sha256 = hashlib.sha256()
    bloco_size = 8 * 1024 * 1024  # 8 MB

    logger.info(f"Calculando SHA256 de: {caminho.name}")
    with open(caminho, "rb") as f:
        for bloco in iter(lambda: f.read(bloco_size), b""):
            sha256.update(bloco)

    digest = sha256.hexdigest()
    logger.info(f"SHA256 calculado: {digest[:16]}... ({caminho.name})")
    return digest
