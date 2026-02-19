"""
============================================================
Módulo de Limpeza — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-02-19
Descrição: Remove arquivos e pastas temporários criados
           durante o processo de exportação e compactação.
           Executado ao final de cada backup para liberar
           espaço em disco.
============================================================
Histórico:
  1.0.0 (2026-02-19) — Versão inicial
============================================================
"""

import shutil
from pathlib import Path

from utils.logger import obter_logger

logger = obter_logger("limpeza")


def limpar_arquivos_temporarios(pasta: Path) -> None:
    """
    Remove uma pasta e todo o seu conteúdo (arquivos e subpastas).

    Usada para limpar a pasta temporária de um colaborador após
    o upload do ZIP para o Google Drive ter sido concluído.

    Args:
        pasta: Caminho da pasta a ser removida
    """
    if not pasta.exists():
        logger.debug(f"Pasta já não existe, nada a limpar: {pasta}")
        return

    try:
        # Calcula o tamanho total antes de remover (para log)
        tamanho_total = sum(
            f.stat().st_size for f in pasta.rglob("*") if f.is_file()
        )
        tamanho_mb = tamanho_total / (1024 * 1024)
        num_arquivos = sum(1 for f in pasta.rglob("*") if f.is_file())

        # Remove a pasta e tudo dentro dela
        shutil.rmtree(pasta)

        logger.info(
            f"Limpeza concluída — Removidos {num_arquivos} arquivo(s) "
            f"({tamanho_mb:.1f} MB) de: {pasta}"
        )

    except Exception as erro:
        # Limpeza falhando não deve impedir o restante do fluxo
        logger.error(f"Erro ao limpar pasta temporária {pasta}: {erro}")
        logger.warning("A limpeza falhou, mas o backup foi concluído com sucesso")


def limpar_arquivo_zip(caminho_zip: Path) -> None:
    """
    Remove um arquivo ZIP individual após o upload para o Drive.

    Args:
        caminho_zip: Caminho do arquivo .zip a ser removido
    """
    if not caminho_zip.exists():
        logger.debug(f"Arquivo ZIP já não existe: {caminho_zip}")
        return

    try:
        tamanho_mb = caminho_zip.stat().st_size / (1024 * 1024)
        caminho_zip.unlink()
        logger.info(f"Arquivo ZIP removido: {caminho_zip} ({tamanho_mb:.1f} MB)")

    except Exception as erro:
        logger.error(f"Erro ao remover arquivo ZIP {caminho_zip}: {erro}")
