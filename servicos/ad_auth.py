"""
============================================================
Módulo de Autenticação no Active Directory — Automação de Backups
============================================================
Versão: 1.0.0
Data: 2026-06-15
Descrição: Autentica usuários contra o Active Directory (LDAP)
           usando a própria credencial informada no login (bind
           direto via UPN). Após o bind, verifica se o usuário
           pertence ao grupo autorizado (busca com regra de
           cadeia, que cobre grupos aninhados).

           O acesso ao dashboard é restrito aos membros do grupo
           definido em AD_GRUPO_AUTORIZADO (padrão:
           "MM - Backup - Admins").
============================================================
Histórico:
  1.0.0 (2026-06-15) — Versão inicial
============================================================
"""

from ldap3 import Server, ServerPool, Connection, Tls, ALL, SUBTREE, SIMPLE, FIRST
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars
import ssl

from config.configuracoes import (
    AD_SERVIDOR,
    AD_PORTA,
    AD_USAR_SSL,
    AD_VALIDAR_CERT,
    AD_DOMINIO_UPN,
    AD_BASE_DN,
    AD_GRUPO_AUTORIZADO,
    AD_TIMEOUT,
)
from utils.logger import obter_logger

logger = obter_logger("ad_auth")

# OID da regra de correspondência "LDAP_MATCHING_RULE_IN_CHAIN" do Active
# Directory. Permite verificar pertencimento a um grupo de forma recursiva
# (cobre grupos aninhados), sem precisar percorrer memberOf manualmente.
_REGRA_CADEIA = "1.2.840.113556.1.4.1941"


class ADConfiguracaoError(RuntimeError):
    """Erro de configuração — variáveis do AD ausentes no .env."""


class ADIndisponivelError(RuntimeError):
    """Erro de infraestrutura — não foi possível contatar o servidor AD."""


def _validar_configuracao() -> None:
    """Garante que as variáveis mínimas do AD estão definidas."""
    faltando = [
        nome
        for nome, valor in (
            ("AD_SERVIDOR", AD_SERVIDOR),
            ("AD_BASE_DN", AD_BASE_DN),
            ("AD_DOMINIO_UPN", AD_DOMINIO_UPN),
        )
        if not valor
    ]
    if faltando:
        raise ADConfiguracaoError(
            "Configuração do Active Directory incompleta. "
            f"Defina no .env: {', '.join(faltando)}."
        )


def _montar_identidade_bind(usuario: str) -> str:
    """
    Monta a identidade UPN usada no bind (autenticação) do AD.

    O login é sempre por username (sAMAccountName), nunca e-mail. Mesmo que
    algo com domínio seja colado, usamos apenas a parte do username e
    montamos o UPN com o sufixo do domínio (AD_DOMINIO_UPN):
        "joao.silva"  → "joao.silva@<AD_DOMINIO_UPN>"
    """
    return f"{_login_curto(usuario)}@{AD_DOMINIO_UPN}"


def _login_curto(usuario: str) -> str:
    """Extrai o sAMAccountName (sem domínio) a partir do que foi digitado."""
    usuario = (usuario or "").strip()
    return usuario.split("@")[0].split("\\")[-1]


def _hosts() -> list[str]:
    """Lista de alvos a partir de AD_SERVIDOR (nome de domínio ou lista)."""
    return [h.strip() for h in (AD_SERVIDOR or "").split(",") if h.strip()]


def _criar_alvo():
    """
    Cria o alvo de conexão do ldap3 conforme AD_SERVIDOR.

    AD_SERVIDOR aceita:
    - o NOME DO DOMÍNIO (ex.: "madeiramadeira.local") — num AD, o DNS resolve
      o nome do domínio para todos os DCs, distribuindo as conexões;
    - uma LISTA separada por vírgula de alvos (domínio/FQDN/IP) — neste caso é
      montado um ServerPool com a estratégia FIRST: tenta os alvos NA ORDEM
      informada (o 1º é o primário; os seguintes são reserva), contornando os
      que estiverem fora do ar.

    Returns:
        Server (alvo único) ou ServerPool (vários alvos: primário + reserva).
    """
    tls = None
    if AD_USAR_SSL:
        validacao = ssl.CERT_REQUIRED if AD_VALIDAR_CERT else ssl.CERT_NONE
        tls = Tls(validate=validacao)

    servidores = [
        Server(
            host,
            port=AD_PORTA,
            use_ssl=AD_USAR_SSL,
            tls=tls,
            get_info=ALL,
            connect_timeout=AD_TIMEOUT,
        )
        for host in _hosts()
    ]

    if len(servidores) == 1:
        return servidores[0]

    # FIRST → respeita a ORDEM (primário primeiro, demais como reserva).
    # active=True → testa disponibilidade; exhaust=True → afasta temporariamente
    # o alvo que falhar, evitando reincidir nele a cada tentativa.
    return ServerPool(servidores, pool_strategy=FIRST, active=True, exhaust=True)


def autenticar(usuario: str, senha: str) -> dict:
    """
    Autentica o usuário no AD e verifica o grupo autorizado.

    Fluxo:
    1. Bind no AD com a credencial informada (UPN). Falha → credencial inválida.
    2. Busca o objeto do usuário (DN, nome e e-mail).
    3. Verifica, com regra de cadeia, se o usuário pertence ao grupo
       AD_GRUPO_AUTORIZADO (cobre grupos aninhados).

    Returns:
        dict com:
        - autenticado (bool): credencial válida no AD
        - autorizado  (bool): pertence ao grupo autorizado
        - usuario     (str):  sAMAccountName normalizado
        - nome        (str):  displayName (ou o login, se ausente)
        - email       (str):  mail (ou "")

    Raises:
        ADConfiguracaoError:  variáveis do AD ausentes no .env.
        ADIndisponivelError:  servidor AD inacessível / erro de transporte.
    """
    _validar_configuracao()

    if not usuario or not senha:
        return {"autenticado": False, "autorizado": False,
                "usuario": usuario, "nome": "", "email": ""}

    identidade = _montar_identidade_bind(usuario)
    login = _login_curto(usuario)

    try:
        servidor = _criar_alvo()
        # raise_exceptions=False → bind() retorna False em credencial inválida
        # (em vez de lançar exceção); erros de socket/SSL ainda sobem.
        conexao = Connection(
            servidor,
            user=identidade,
            password=senha,
            authentication=SIMPLE,
            raise_exceptions=False,
        )

        if not conexao.bind():
            logger.warning(f"Falha de autenticação no AD para '{login}' (credencial inválida)")
            return {"autenticado": False, "autorizado": False,
                    "usuario": login, "nome": "", "email": ""}

        # ── Usuário autenticado: busca dados e valida o grupo ──────────────
        filtro_usuario = (
            f"(&(objectClass=user)"
            f"(|(userPrincipalName={escape_filter_chars(identidade)})"
            f"(sAMAccountName={escape_filter_chars(login)})))"
        )
        conexao.search(
            AD_BASE_DN,
            filtro_usuario,
            search_scope=SUBTREE,
            attributes=["distinguishedName", "displayName", "mail", "sAMAccountName"],
        )

        if not conexao.entries:
            logger.warning(
                f"Usuário '{login}' autenticou mas não foi localizado em {AD_BASE_DN}"
            )
            conexao.unbind()
            return {"autenticado": True, "autorizado": False,
                    "usuario": login, "nome": login, "email": ""}

        entrada = conexao.entries[0]
        user_dn = str(entrada.distinguishedName)
        nome = str(entrada.displayName) if entrada.displayName else login
        email = str(entrada.mail) if entrada.mail else ""

        autorizado = _pertence_ao_grupo(conexao, user_dn)
        conexao.unbind()

        if autorizado:
            logger.info(f"Login autorizado: '{login}' (grupo '{AD_GRUPO_AUTORIZADO}')")
        else:
            logger.warning(
                f"Login negado: '{login}' não pertence ao grupo '{AD_GRUPO_AUTORIZADO}'"
            )

        return {"autenticado": True, "autorizado": autorizado,
                "usuario": login, "nome": nome, "email": email}

    except LDAPException as erro:
        logger.error(f"Erro de comunicação com o AD: {erro}")
        raise ADIndisponivelError(
            "Não foi possível contatar o servidor de autenticação (AD)."
        ) from erro


def _pertence_ao_grupo(conexao: Connection, user_dn: str) -> bool:
    """
    Verifica se o usuário (DN) pertence ao grupo autorizado, de forma
    recursiva (regra de cadeia do AD — cobre grupos aninhados).
    """
    filtro_grupo = (
        f"(&(objectClass=group)"
        f"(cn={escape_filter_chars(AD_GRUPO_AUTORIZADO)})"
        f"(member:{_REGRA_CADEIA}:={escape_filter_chars(user_dn)}))"
    )
    conexao.search(AD_BASE_DN, filtro_grupo, search_scope=SUBTREE, attributes=["cn"])
    return len(conexao.entries) > 0
