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
from datetime import datetime, timedelta
from pathlib import Path

from config.configuracoes import (
    PASTA_LOGS, LOGS_RETENCAO_DIAS, LOGS_TAMANHO_MAXIMO_BYTES,
    NAS_SYNC_DIR, NAS_SYNC_RETENCAO_HORAS,
)
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


def limpar_logs_antigos() -> None:
    """
    Limpa a pasta de logs aplicando duas regras, nesta ordem:

    1. RETENÇÃO POR TEMPO: remove arquivos com mais de LOGS_RETENCAO_DIAS dias.
    2. LIMITE DE TAMANHO: se a pasta ainda ultrapassar LOGS_TAMANHO_MAXIMO_BYTES,
       remove os arquivos mais antigos primeiro até o total ficar dentro do limite.

    Em ambos os casos, o arquivo de log ativo (.log sem sufixo numérico) é
    truncado em vez de excluído — assim o processo não perde o handler de escrita.

    Chamada automaticamente na inicialização do servidor.
    """
    tamanho_maximo_gb = LOGS_TAMANHO_MAXIMO_BYTES / (1024 ** 3)
    logger.info(
        f"Iniciando limpeza de logs — "
        f"retenção: {LOGS_RETENCAO_DIAS} dias | "
        f"limite: {tamanho_maximo_gb:.0f} GB"
    )

    def _todos_os_logs():
        """Retorna todos os arquivos .log* ordenados do mais antigo para o mais recente."""
        return sorted(
            PASTA_LOGS.glob("*.log*"),
            key=lambda f: f.stat().st_mtime,
        )

    def _remover_ou_truncar(arquivo: Path) -> float:
        """
        Remove backups rotativos (.log.1, .log.2, ...) ou trunca o arquivo
        ativo (.log) para não quebrar o handler em execução.
        Retorna o tamanho liberado em bytes.
        """
        tamanho = arquivo.stat().st_size
        # Arquivo ativo (sem sufixo numérico): trunca para não invalidar o handler
        if arquivo.suffix == ".log":
            arquivo.write_text("", encoding="utf-8")
            logger.info(f"Log ativo truncado: {arquivo.name} ({tamanho / (1024**2):.1f} MB liberados)")
        else:
            arquivo.unlink()
            logger.info(f"Log removido: {arquivo.name} ({tamanho / (1024**2):.1f} MB liberados)")
        return tamanho

    # ── REGRA 1: retenção por tempo ──────────────────────────────────────────
    limite_data = datetime.now() - timedelta(days=LOGS_RETENCAO_DIAS)
    removidos_tempo = 0
    liberados_tempo = 0

    for arquivo in _todos_os_logs():
        try:
            modificado_em = datetime.fromtimestamp(arquivo.stat().st_mtime)
            if modificado_em < limite_data:
                liberados_tempo += _remover_ou_truncar(arquivo)
                removidos_tempo += 1
        except Exception as erro:
            logger.error(f"Erro ao processar log {arquivo.name} (regra de tempo): {erro}")

    if removidos_tempo:
        logger.info(
            f"Regra de tempo: {removidos_tempo} arquivo(s) tratado(s), "
            f"{liberados_tempo / (1024**2):.1f} MB liberados"
        )
    else:
        logger.info(f"Regra de tempo: nenhum arquivo anterior a {limite_data.strftime('%Y-%m-%d')}")

    # ── REGRA 2: limite de tamanho ───────────────────────────────────────────
    tamanho_atual = sum(f.stat().st_size for f in PASTA_LOGS.glob("*.log*") if f.exists())
    tamanho_atual_gb = tamanho_atual / (1024 ** 3)

    if tamanho_atual <= LOGS_TAMANHO_MAXIMO_BYTES:
        logger.info(
            f"Regra de tamanho: {tamanho_atual_gb:.2f} GB — dentro do limite de "
            f"{tamanho_maximo_gb:.0f} GB, nenhuma ação necessária"
        )
        return

    logger.warning(
        f"Regra de tamanho: {tamanho_atual_gb:.2f} GB excedem o limite de "
        f"{tamanho_maximo_gb:.0f} GB — removendo os mais antigos..."
    )

    removidos_tamanho = 0
    liberados_tamanho = 0

    for arquivo in _todos_os_logs():
        if tamanho_atual <= LOGS_TAMANHO_MAXIMO_BYTES:
            break
        try:
            liberado = _remover_ou_truncar(arquivo)
            tamanho_atual -= liberado
            liberados_tamanho += liberado
            removidos_tamanho += 1
        except Exception as erro:
            logger.error(f"Erro ao processar log {arquivo.name} (regra de tamanho): {erro}")

    logger.info(
        f"Regra de tamanho: {removidos_tamanho} arquivo(s) tratado(s), "
        f"{liberados_tamanho / (1024**2):.1f} MB liberados — "
        f"total atual: {tamanho_atual / (1024**3):.2f} GB"
    )


def limpar_zips_sincronizados() -> None:
    """Safety-net: apaga ZIPs em NAS_SYNC_DIR com mais de NAS_SYNC_RETENCAO_HORAS.

    A exclusao normal do ZIP local acontece na finalizacao do backup
    (processamento/finalizacao_nas.py), apos a janela NAS_SYNC_HORAS_ESPERA.
    Esta varredura roda no boot do servidor e cobre ZIPs orfaos — sem registro
    no banco, que nunca passam pela finalizacao (ex: disponibilizados manualmente).

    Fluxo:
      1. Servidor moveu o ZIP para NAS_SYNC_DIR/<email>/<arquivo>.zip
      2. O NAS Synology sincroniza essa pasta por conta propria (sem markers)
      3. Esta funcao varre por *.zip. Se o mtime do ZIP for mais antigo que a
         retencao, apaga o arquivo — presume-se que o NAS ja sincronizou.

    Seguranca: se NAS_SYNC_DIR nao existir ou estiver vazio, nao faz nada.
    """
    if not NAS_SYNC_DIR.exists():
        logger.debug(f"NAS_SYNC_DIR nao existe — pulando limpeza: {NAS_SYNC_DIR}")
        return

    logger.info(
        f"Iniciando limpeza de ZIPs sincronizados com NAS — "
        f"retencao: {NAS_SYNC_RETENCAO_HORAS}h | base: {NAS_SYNC_DIR}"
    )

    limite_data = datetime.now() - timedelta(hours=NAS_SYNC_RETENCAO_HORAS)

    apagados = 0
    liberado_total = 0
    pendentes = 0

    # rglob captura ZIPs em subpastas por email
    for zip_arquivo in NAS_SYNC_DIR.rglob("*.zip"):
        try:
            modificado_em = datetime.fromtimestamp(zip_arquivo.stat().st_mtime)
            if modificado_em >= limite_data:
                pendentes += 1
                continue

            tamanho = zip_arquivo.stat().st_size
            zip_arquivo.unlink()
            liberado_total += tamanho
            apagados += 1
            logger.info(
                f"ZIP sincronizado apagado: {zip_arquivo.name} "
                f"({tamanho / (1024**2):.1f} MB) — de {modificado_em.strftime('%Y-%m-%d')}"
            )

        except Exception as erro:
            logger.error(f"Erro ao processar ZIP {zip_arquivo.name}: {erro}")

    if apagados or pendentes:
        logger.info(
            f"Limpeza NAS: {apagados} ZIP(s) apagado(s) "
            f"({liberado_total / (1024**3):.2f} GB liberados) | "
            f"{pendentes} ainda dentro do periodo de retencao."
        )
    else:
        logger.info("Limpeza NAS: nenhum ZIP encontrado.")
