"""
============================================================
Módulo de Autenticação LOCAL (break-glass) — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-06-15
Descrição: Autentica um único usuário administrador LOCAL, sem
           Active Directory. Serve como acesso de emergência
           (break-glass) quando o AD está indisponível ou antes
           de o login corporativo estar configurado.

           A senha é validada contra um HASH (Werkzeug) guardado
           no .env — nunca em texto puro. O login local só fica
           ativo se ADM_LOCAL_USUARIO e ADM_LOCAL_SENHA_HASH
           estiverem ambos definidos.
============================================================
Histórico:
  1.0.0 (2026-06-15) — Versão inicial
============================================================
"""

from werkzeug.security import check_password_hash

from config.configuracoes import ADM_LOCAL_USUARIO, ADM_LOCAL_SENHA_HASH
from utils.logger import obter_logger

logger = obter_logger("auth_local")


def local_habilitado() -> bool:
    """Indica se o administrador local está configurado (usuário + hash)."""
    return bool(ADM_LOCAL_USUARIO and ADM_LOCAL_SENHA_HASH)


def autenticar_local(usuario: str, senha: str) -> bool:
    """
    Valida a credencial do administrador local.

    Compara o usuário (case-insensitive) e verifica a senha contra o hash
    configurado. Retorna False se o login local não estiver habilitado.
    """
    if not local_habilitado():
        return False

    usuario_norm = (usuario or "").strip().lower()
    if usuario_norm != ADM_LOCAL_USUARIO.strip().lower():
        return False

    if not check_password_hash(ADM_LOCAL_SENHA_HASH, senha or ""):
        logger.warning(f"Falha de autenticação do admin local '{usuario_norm}' (senha incorreta)")
        return False

    return True
