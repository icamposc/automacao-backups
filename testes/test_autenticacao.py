"""
Testes da autenticação no AD e da proteção do dashboard (app/autenticacao.py).

Verifica:
- Rotas protegidas (/dashboard e /api/backups/*) exigem login.
- /dashboard sem sessão → redireciona para /login (302).
- /api/backups/* sem sessão → 401 JSON (para o fetch do dashboard).
- POST /login: sucesso cria sessão; credencial inválida e usuário sem
  permissão retornam erro (401) sem criar sessão.
- /logout encerra a sessão.
- Rotas abertas (/health, webhook) NÃO são bloqueadas pelo guard de login.
"""

from unittest.mock import patch

from werkzeug.security import generate_password_hash

import servicos.ad_auth as ad_auth
import servicos.auth_local as auth_local
import app.autenticacao as autenticacao
from utils import auditoria


# ─── Normalização do username (login só por usuário, nunca e-mail) ──────────

class TestIdentidadeBind:
    def test_username_puro_vira_upn(self, monkeypatch):
        monkeypatch.setattr(ad_auth, "AD_DOMINIO_UPN", "madeiramadeira.local")
        assert ad_auth._montar_identidade_bind("joao.silva") == "joao.silva@madeiramadeira.local"

    def test_email_colado_usa_apenas_o_username(self, monkeypatch):
        monkeypatch.setattr(ad_auth, "AD_DOMINIO_UPN", "madeiramadeira.local")
        # Mesmo colando um e-mail, o bind usa só o username + sufixo configurado.
        assert ad_auth._montar_identidade_bind("joao.silva@outro.com") == "joao.silva@madeiramadeira.local"

    def test_formato_dominio_nt_usa_apenas_o_username(self, monkeypatch):
        monkeypatch.setattr(ad_auth, "AD_DOMINIO_UPN", "madeiramadeira.local")
        assert ad_auth._montar_identidade_bind("MADEIRA\\joao.silva") == "joao.silva@madeiramadeira.local"


# ─── Alvo de conexão (domínio único × pool de DCs com failover) ────────────

class TestAlvoConexao:
    def test_um_host_retorna_server_unico(self, monkeypatch):
        from ldap3 import Server
        monkeypatch.setattr(ad_auth, "AD_SERVIDOR", "madeiramadeira.local")
        assert ad_auth._hosts() == ["madeiramadeira.local"]
        assert isinstance(ad_auth._criar_alvo(), Server)

    def test_varios_hosts_viram_pool_com_failover(self, monkeypatch):
        from ldap3 import ServerPool
        monkeypatch.setattr(
            ad_auth, "AD_SERVIDOR", "dc01.dom.local, dc02.dom.local , 10.0.0.10"
        )
        assert ad_auth._hosts() == ["dc01.dom.local", "dc02.dom.local", "10.0.0.10"]
        assert isinstance(ad_auth._criar_alvo(), ServerPool)


# ─── Resultados simulados de servicos.ad_auth.autenticar ───────────────────

_SUCESSO = {
    "autenticado": True, "autorizado": True,
    "usuario": "joao.silva", "nome": "João Silva", "email": "joao.silva@empresa.com",
}
_CREDENCIAL_INVALIDA = {
    "autenticado": False, "autorizado": False,
    "usuario": "joao.silva", "nome": "", "email": "",
}
_SEM_PERMISSAO = {
    "autenticado": True, "autorizado": False,
    "usuario": "joao.silva", "nome": "João Silva", "email": "",
}


def _login(cliente, resultado):
    """Faz POST /login com o resultado do AD mockado."""
    with patch("app.autenticacao.autenticar", return_value=resultado):
        return cliente.post(
            "/login",
            data={"usuario": "joao.silva", "senha": "segredo"},
            follow_redirects=False,
        )


# ─── Proteção das rotas ────────────────────────────────────────────────────

class TestProtecao:
    def test_dashboard_sem_sessao_redireciona_para_login(self, cliente_flask):
        resp = cliente_flask.get("/dashboard")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_api_sem_sessao_retorna_401(self, cliente_flask):
        resp = cliente_flask.get("/api/backups/resumo")
        assert resp.status_code == 401
        assert resp.get_json()["erro"] == "Não autenticado"

    def test_login_get_acessivel_sem_sessao(self, cliente_flask):
        resp = cliente_flask.get("/login")
        assert resp.status_code == 200
        assert b"Automa" in resp.data  # renderizou a tela de login


class TestRotasAbertas:
    def test_health_nao_e_bloqueado_pelo_login(self, cliente_flask):
        resp = cliente_flask.get("/health")
        # /health pode ser 200 ou 503 (estado dos componentes), mas NUNCA
        # 302 (redirect de login) nem 401 (guard de sessão).
        assert resp.status_code not in (302, 401)

    def test_webhook_chega_ao_handler_hmac_nao_ao_guard(self, cliente_flask):
        # Sem assinatura válida, o webhook (HMAC próprio) responde 401 com seu
        # corpo — provando que o guard de login NÃO o bloqueou antes.
        resp = cliente_flask.post("/webhook/backup-desligado", json={"x": 1})
        assert resp.status_code == 401
        assert "webhook" in resp.get_json()["erro"].lower()


# ─── Fluxo de login ────────────────────────────────────────────────────────

class TestLogin:
    def test_login_sucesso_cria_sessao_e_redireciona(self, cliente_flask):
        resp = _login(cliente_flask, _SUCESSO)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/dashboard")
        with cliente_flask.session_transaction() as sess:
            assert sess["usuario"] == "joao.silva"
            assert sess["nome"] == "João Silva"

    def test_login_credencial_invalida_nao_cria_sessao(self, cliente_flask):
        resp = _login(cliente_flask, _CREDENCIAL_INVALIDA)
        assert resp.status_code == 401
        assert "inv" in resp.get_data(as_text=True).lower()  # "inválidos"
        with cliente_flask.session_transaction() as sess:
            assert "usuario" not in sess

    def test_login_sem_permissao_nega_acesso(self, cliente_flask):
        resp = _login(cliente_flask, _SEM_PERMISSAO)
        assert resp.status_code == 401
        assert "negado" in resp.get_data(as_text=True).lower()
        with cliente_flask.session_transaction() as sess:
            assert "usuario" not in sess

    def test_login_campos_vazios_nao_chama_ad(self, cliente_flask):
        with patch("app.autenticacao.autenticar") as mock_aut:
            resp = cliente_flask.post("/login", data={"usuario": "", "senha": ""})
        assert resp.status_code == 401
        mock_aut.assert_not_called()

    def test_dashboard_acessivel_apos_login(self, cliente_flask):
        with cliente_flask.session_transaction() as sess:
            sess["usuario"] = "joao.silva"
            sess["nome"] = "João Silva"
        resp = cliente_flask.get("/dashboard")
        assert resp.status_code == 200

    def test_logout_encerra_sessao(self, cliente_flask):
        with cliente_flask.session_transaction() as sess:
            sess["usuario"] = "joao.silva"
        resp = cliente_flask.get("/logout")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]
        with cliente_flask.session_transaction() as sess:
            assert "usuario" not in sess


# ─── Administrador LOCAL (break-glass, sem AD) ─────────────────────────────

class TestAdminLocal:
    def _configurar(self, monkeypatch, usuario="admin.local", senha="segredo123"):
        h = generate_password_hash(senha)
        monkeypatch.setattr(auth_local, "ADM_LOCAL_USUARIO", usuario)
        monkeypatch.setattr(auth_local, "ADM_LOCAL_SENHA_HASH", h)
        monkeypatch.setattr(autenticacao, "ADM_LOCAL_USUARIO", usuario)

    def test_autenticar_local_ok_e_case_insensitive(self, monkeypatch):
        self._configurar(monkeypatch)
        assert auth_local.autenticar_local("admin.local", "segredo123") is True
        assert auth_local.autenticar_local("ADMIN.LOCAL", "segredo123") is True

    def test_autenticar_local_senha_errada(self, monkeypatch):
        self._configurar(monkeypatch)
        assert auth_local.autenticar_local("admin.local", "errada") is False

    def test_local_desabilitado_sem_config(self, monkeypatch):
        monkeypatch.setattr(auth_local, "ADM_LOCAL_USUARIO", "")
        monkeypatch.setattr(auth_local, "ADM_LOCAL_SENHA_HASH", "")
        assert auth_local.local_habilitado() is False
        assert auth_local.autenticar_local("admin.local", "x") is False

    def test_login_local_cria_sessao_tipo_local(self, cliente_flask, monkeypatch):
        self._configurar(monkeypatch)
        resp = cliente_flask.post(
            "/login", data={"usuario": "admin.local", "senha": "segredo123"}
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/dashboard")
        with cliente_flask.session_transaction() as sess:
            assert sess["usuario"] == "admin.local"
            assert sess["tipo"] == "local"

    def test_login_local_nao_consulta_ad(self, cliente_flask, monkeypatch):
        # Admin local autentica antes do AD: o AD nem deve ser chamado.
        self._configurar(monkeypatch)
        with patch("app.autenticacao.autenticar") as mock_ad:
            resp = cliente_flask.post(
                "/login", data={"usuario": "admin.local", "senha": "segredo123"}
            )
        assert resp.status_code == 302
        mock_ad.assert_not_called()


# ─── Auditoria ─────────────────────────────────────────────────────────────

class TestAuditoria:
    def test_registrar_nao_lanca_fora_de_contexto(self):
        # Fora de uma requisição, deve usar contexto "sistema" sem erro.
        auditoria.registrar("acao_teste", resultado="ok", detalhe="x")

    def test_login_local_registra_auditoria(self, cliente_flask, monkeypatch):
        h = generate_password_hash("segredo123")
        monkeypatch.setattr(auth_local, "ADM_LOCAL_USUARIO", "admin.local")
        monkeypatch.setattr(auth_local, "ADM_LOCAL_SENHA_HASH", h)
        monkeypatch.setattr(autenticacao, "ADM_LOCAL_USUARIO", "admin.local")
        with patch.object(auditoria, "registrar") as mock_aud:
            cliente_flask.post("/login", data={"usuario": "admin.local", "senha": "segredo123"})
        mock_aud.assert_called()
        # Primeira chamada deve ser o login com método local
        args, kwargs = mock_aud.call_args
        assert args[0] == "login"
        assert kwargs.get("metodo") == "local"
