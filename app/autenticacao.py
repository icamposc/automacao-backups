"""
============================================================
Módulo de Autenticação — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-06-15
Descrição: Blueprint Flask com as rotas de login/logout e o
           guard de proteção do dashboard.

           A autenticação é feita no Active Directory (ver
           servicos/ad_auth.py): bind com a credencial do
           próprio usuário + verificação de pertencimento ao
           grupo autorizado. A sessão é mantida por cookie
           assinado (sessão nativa do Flask).

           Rotas:
           - GET  /login   → formulário de login
           - POST /login   → autentica no AD e cria a sessão
           - GET  /logout  → encerra a sessão

           Proteção:
           - exigir_login() é registrado como before_request no
             servidor e bloqueia qualquer rota do blueprint
             'dashboard' (/dashboard e /api/backups/*) quando
             não há sessão autenticada.
============================================================
Histórico:
  1.0.0 (2026-06-15) — Versão inicial
============================================================
"""

from flask import (
    Blueprint,
    request,
    session,
    redirect,
    url_for,
    render_template,
    jsonify,
)

from servicos.ad_auth import autenticar, ADConfiguracaoError, ADIndisponivelError
from servicos.auth_local import autenticar_local, ADM_LOCAL_USUARIO
from utils.logger import obter_logger
from utils import auditoria

logger = obter_logger("autenticacao")

bp = Blueprint("auth", __name__)

# Nome do blueprint cujas rotas são protegidas pelo login.
_BLUEPRINT_PROTEGIDO = "dashboard"


def usuario_logado() -> bool:
    """Indica se há um usuário autenticado na sessão atual."""
    return bool(session.get("usuario"))


def exigir_login():
    """
    Guard registrado como before_request no servidor.

    Protege todas as rotas do blueprint 'dashboard' (/dashboard e
    /api/backups/*). Demais rotas (webhook, /health, /login, raiz) ficam
    abertas — o webhook tem autenticação própria por HMAC.

    Comportamento quando não autenticado:
    - Requisições de API (/api/...) → 401 JSON (para o fetch do dashboard).
    - Página /dashboard             → redireciona para a tela de login.
    """
    if request.blueprint != _BLUEPRINT_PROTEGIDO:
        return None  # rota não protegida

    if usuario_logado():
        return None  # já autenticado

    if request.path.startswith("/api/"):
        return jsonify({"erro": "Não autenticado", "login_url": url_for("auth.login")}), 401

    return redirect(url_for("auth.login", next=request.path))


@bp.route("/login", methods=["GET"])
def login():
    """Exibe o formulário de login (ou redireciona se já autenticado)."""
    if usuario_logado():
        return redirect(url_for("dashboard.painel"))
    return render_template("login.html", erro=None, proximo=request.args.get("next", ""))


@bp.route("/login", methods=["POST"])
def login_post():
    """Processa o formulário de login autenticando no Active Directory."""
    usuario = (request.form.get("usuario") or "").strip()
    senha = request.form.get("senha") or ""
    proximo = request.form.get("next", "")

    if not usuario or not senha:
        return _render_erro("Informe usuário e senha.", proximo)

    # ── 1) Administrador LOCAL (break-glass, sem AD) ───────────────────────
    # Tentado antes do AD para garantir acesso mesmo com o AD indisponível.
    if autenticar_local(usuario, senha):
        _iniciar_sessao(usuario=ADM_LOCAL_USUARIO, nome="Administrador Local",
                        email="", tipo="local")
        auditoria.registrar("login", resultado="sucesso", metodo="local")
        logger.info(f"Sessão iniciada (admin local) para '{ADM_LOCAL_USUARIO}'")
        return _redirecionar(proximo)

    # ── 2) Active Directory ────────────────────────────────────────────────
    try:
        resultado = autenticar(usuario, senha)
    except ADConfiguracaoError as erro:
        logger.error(f"Login indisponível — configuração do AD: {erro}")
        return _render_erro(
            "Autenticação não configurada. Procure o administrador do sistema.", proximo
        )
    except ADIndisponivelError as erro:
        logger.error(f"Login indisponível — AD inacessível: {erro}")
        auditoria.registrar("login", resultado="erro", metodo="ad",
                            usuario_tentado=usuario, motivo="ad_indisponivel")
        return _render_erro(
            "Servidor de autenticação indisponível. Tente novamente em instantes.", proximo
        )

    if not resultado["autenticado"]:
        auditoria.registrar("login", resultado="falha", metodo="ad",
                            usuario_tentado=usuario, motivo="credencial_invalida")
        return _render_erro("Usuário ou senha inválidos.", proximo)

    if not resultado["autorizado"]:
        auditoria.registrar("login", resultado="negado", metodo="ad",
                            usuario_tentado=resultado["usuario"], motivo="fora_do_grupo")
        return _render_erro(
            "Acesso negado. Seu usuário não pertence ao grupo autorizado.", proximo
        )

    # Sucesso no AD.
    _iniciar_sessao(usuario=resultado["usuario"], nome=resultado.get("nome") or resultado["usuario"],
                    email=resultado.get("email", ""), tipo="ad")
    auditoria.registrar("login", resultado="sucesso", metodo="ad")
    logger.info(f"Sessão iniciada para '{resultado['usuario']}'")
    return _redirecionar(proximo)


def _iniciar_sessao(usuario: str, nome: str, email: str, tipo: str) -> None:
    """Cria a sessão autenticada (comum ao AD e ao admin local)."""
    session.clear()
    session["usuario"] = usuario
    session["nome"] = nome
    session["email"] = email
    session["tipo"] = tipo  # "ad" | "local"
    session.permanent = True


def _redirecionar(proximo: str):
    """Redireciona para o destino interno seguro (evita open-redirect)."""
    destino = proximo if proximo.startswith("/") and not proximo.startswith("//") else None
    return redirect(destino or url_for("dashboard.painel"))


@bp.route("/logout", methods=["GET"])
def logout():
    """Encerra a sessão atual e volta para a tela de login."""
    usuario = session.get("usuario")
    if usuario:
        auditoria.registrar("logout", resultado="ok")
        logger.info(f"Sessão encerrada para '{usuario}'")
    session.clear()
    return redirect(url_for("auth.login"))


def _render_erro(mensagem: str, proximo: str):
    """Renderiza o formulário de login com uma mensagem de erro (HTTP 401)."""
    return render_template("login.html", erro=mensagem, proximo=proximo), 401
