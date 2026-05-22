"""
============================================================
Modulo NAS Synology — Disponibilizacao de Backups
============================================================
Versao: 1.0.0
Data: 2026-05-22
Descricao: Move o ZIP finalizado para NAS_SYNC_DIR/<email>/<arquivo>.zip
           e cria um marker <arquivo>.zip.ready ao lado, sinalizando ao
           NAS que pode coletar.

           Quando o NAS conclui a copia, ele renomeia o marker para
           <arquivo>.zip.uploaded — isso e auditado pela limpeza
           periodica em processamento/limpeza.py.

           NAO usa rede: a transferencia para o NAS e feita pelo proprio
           Synology lendo o disco do servidor (montado via SMB/NFS no NAS).
           Assim, o servidor para de ser gargalo de upload.

           Mesma assinatura do servicos/drive_upload.fazer_upload para
           permitir fallback transparente no orquestrador.
============================================================
Historico:
  1.0.0 (2026-05-22) — Versao inicial
============================================================
"""

import shutil
from pathlib import Path

from config.configuracoes import NAS_SYNC_DIR
from utils.logger import obter_logger

logger = obter_logger("nas_sync")


class ErroNasSync(Exception):
    """Levantada quando a disponibilizacao para o NAS falha."""


def _pasta_destino_email(email: str) -> Path:
    """Retorna a pasta dedicada ao email dentro de NAS_SYNC_DIR.

    Estrutura: NAS_SYNC_DIR/<email>/<arquivo>.zip
    """
    return NAS_SYNC_DIR / email


def disponibilizar_para_nas(
    caminho_arquivo: Path,
    nome_arquivo: str = None,
    sha256: str = None,
    on_progresso: callable = None,
) -> dict:
    """Move o ZIP para NAS_SYNC_DIR e cria um marker .ready para o NAS coletar.

    Mantem a mesma assinatura de servicos/drive_upload.fazer_upload para
    permitir fallback transparente no orquestrador.

    O nome do arquivo (com timestamp) ja vem unico, entao nao havera colisao
    quando o mesmo email tiver multiplos backups historicos.

    Args:
        caminho_arquivo: Caminho local do ZIP finalizado.
        nome_arquivo:    Nome a ser usado no destino. Default = nome do arquivo.
        sha256:          Hash SHA256. Gravado em <arquivo>.zip.sha256 ao lado.
        on_progresso:    Callback opcional chamado com 0 e 100 (operacao e instantanea).

    Returns:
        Dict com chaves compativeis com drive_upload:
            - 'id':          mesmo formato (path absoluto, usado como id local)
            - 'name':        nome do arquivo no destino
            - 'webViewLink': pseudo-URI 'nas:<path>' identificando o local

    Raises:
        ErroNasSync: Falha de I/O (permissao, disco cheio, NAS_SYNC_DIR ausente).
        FileNotFoundError: caminho_arquivo nao existe.
    """
    if not caminho_arquivo.exists():
        raise FileNotFoundError(f"Arquivo nao encontrado: {caminho_arquivo}")

    nome = nome_arquivo or caminho_arquivo.name
    tamanho_mb = caminho_arquivo.stat().st_size / (1024 * 1024)

    # O email vai como subdiretorio para separar por colaborador.
    # Removemos a extensao .zip do nome para extrair o email do prefixo.
    # Padrao esperado: <email>_YYYYMMDD_HHMMSS.zip
    email = caminho_arquivo.stem.rsplit("_", 2)[0] if "_" in caminho_arquivo.stem else "_sem_email"

    pasta_destino = _pasta_destino_email(email)
    destino_zip = pasta_destino / nome
    destino_ready = pasta_destino / f"{nome}.ready"

    logger.info(
        f"Disponibilizando para NAS: {nome} ({tamanho_mb:.1f} MB) -> {destino_zip}"
    )

    if on_progresso:
        try:
            on_progresso(0)
        except Exception:
            pass

    try:
        pasta_destino.mkdir(parents=True, exist_ok=True)

        # shutil.move usa os.rename quando origem e destino estao no mesmo filesystem
        # (instantaneo), e cai para copy+delete entre filesystems diferentes.
        shutil.move(str(caminho_arquivo), str(destino_zip))

        # Marker .ready criado APOS o move bem-sucedido — assim o NAS nunca enxerga
        # um arquivo half-written. Conteudo: SHA256 (se disponivel) para o NAS validar.
        conteudo_marker = sha256 or ""
        destino_ready.write_text(conteudo_marker, encoding="utf-8")

    except OSError as erro:
        # Mantem caminho_arquivo intacto se o move falhou no meio
        logger.error(f"Falha ao disponibilizar para NAS: {erro}")
        raise ErroNasSync(
            f"Falha ao mover {nome} para {pasta_destino}: {erro}"
        ) from erro

    if on_progresso:
        try:
            on_progresso(100)
        except Exception:
            pass

    pseudo_uri = f"nas:{destino_zip}"
    logger.info(f"Disponibilizado no NAS — {nome} | marker .ready criado | link={pseudo_uri}")

    return {
        "id": str(destino_zip),
        "name": nome,
        "webViewLink": pseudo_uri,
    }
