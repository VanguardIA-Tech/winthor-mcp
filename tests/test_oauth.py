"""O fluxo OAuth 2.1 de ponta a ponta, contra o app HTTP real.

Prova o que importa: a pessoa faz login com a credencial WinThor dela e o token
resultante abre uma sessão com a identidade dela — a base do "cada um executa
com o próprio usuário". A autenticação é dublada para não depender do Oracle.
"""

import re

import httpx
import pytest
from asgi_lifespan import LifespanManager

from winthor_mcp.modelos import Identidade

_VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("WINTHOR_DB_HOST", "x")
    monkeypatch.setenv("WINTHOR_DB_SERVICE", "y")
    monkeypatch.setenv("WINTHOR_DB_USER", "Z")
    monkeypatch.setenv("WINTHOR_DB_PASSWORD", "p")
    monkeypatch.setenv("WINTHOR_PUBLIC_URL", "http://127.0.0.1:8899")

    import winthor_mcp.configuracao as cfg

    cfg.configuracao.cache_clear()

    import importlib

    import winthor_mcp.auth.provedor as prov
    from winthor_mcp.identidade import Autenticado

    async def fake_auth(mat, senha):
        if mat == "22" and senha == "senha-de-teste":
            ident = Identidade(matricula=22, nome="FULANO", situacao="A")
            return Autenticado(identidade=ident, usuario_wta="FULANO.SILVA", credencial_wta="MD5")
        return None

    monkeypatch.setattr(prov, "autenticar_para_sessao", fake_auth)

    import winthor_mcp.servidor as servidor

    importlib.reload(servidor)
    yield servidor.mcp.http_app()
    cfg.configuracao.cache_clear()


async def _cliente(app):
    lm = LifespanManager(app)
    await lm.__aenter__()
    cli = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8899"
    )
    return cli, lm


async def test_fluxo_completo_abre_sessao_da_pessoa(app):
    cli, lm = await _cliente(app)
    try:
        # descoberta + registro dinâmico
        assert (await cli.get("/.well-known/oauth-authorization-server")).status_code == 200
        reg = await cli.post(
            "/register",
            json={
                "client_name": "teste",
                "redirect_uris": ["http://cli/cb"],
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
        )
        client_id = reg.json()["client_id"]

        # authorize manda para o login próprio
        r = await cli.get(
            "/authorize",
            params={
                "client_id": client_id,
                "redirect_uri": "http://cli/cb",
                "response_type": "code",
                "code_challenge": _CHALLENGE,
                "code_challenge_method": "S256",
                "state": "xyz",
            },
            follow_redirects=False,
        )
        pedido = re.search(r"pedido=([\w-]+)", r.headers["location"]).group(1)

        # senha errada não vira código
        errado = await cli.post(
            "/winthor/login", data={"pedido": pedido, "matricula": "22", "senha": "nao"}
        )
        assert "incorreta" in errado.text

        # senha certa volta ao cliente com código e state
        ok = await cli.post(
            "/winthor/login",
            data={"pedido": pedido, "matricula": "22", "senha": "senha-de-teste"},
            follow_redirects=False,
        )
        destino = ok.headers["location"]
        assert "state=xyz" in destino
        code = re.search(r"code=([\w-]+)", destino).group(1)

        # troca o código pelo token e a sessão nasce com a identidade certa
        tok = await cli.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "http://cli/cb",
                "client_id": client_id,
                "code_verifier": _VERIFIER,
            },
        )
        access = tok.json()["access_token"]

        from winthor_mcp.sessao import usuario as sessao

        atual = sessao.obter(access)
        assert atual is not None
        assert atual.identidade.matricula == 22
        assert sessao.obter("wt_inexistente") is None
    finally:
        await cli.aclose()
        await lm.__aexit__(None, None, None)
