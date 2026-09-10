"""O servidor de autorização OAuth 2.1 com login no WinThor.

O cliente MCP (Claude, Cursor) fala OAuth padrão: descobre os metadados, se
registra (DCR), manda a pessoa autorizar e troca o código por um token. A única
parte própria é a tela de autorização: em vez de aprovar sozinha, ela pede
matrícula e senha do WinThor e valida com `autenticar` — a mesma checagem do
resto do servidor, que funciona offline pela cifra do SENHABD ou pelo WTA.

O token de acesso é opaco e É a chave da sessão: enquanto ele vale, existe uma
`SessaoUsuario` com a identidade da pessoa e, quando há WTA, a credencial para
renovar o token dela de 4 em 4 horas. Assim cada execução no WinThor sai no
nome de quem pediu, e a auditoria do ERP registra a matrícula certa.
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from fastmcp.server.auth.auth import ClientRegistrationOptions, OAuthProvider
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from winthor_mcp.auth.paginas import pagina_login
from winthor_mcp.configuracao import configuracao
from winthor_mcp.identidade import Autenticado, SemComoValidar, autenticar_para_sessao
from winthor_mcp.sessao import usuario as sessao

_VALIDADE_CODE_SEGUNDOS = 5 * 60


class _Pendencia:
    """Uma autorização que começou e espera a pessoa fazer login."""

    __slots__ = ("client", "params", "criada_em")

    def __init__(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> None:
        self.client = client
        self.params = params
        self.criada_em = time.time()


class _CodeAutenticado:
    """Um código de autorização já ligado a uma pessoa autenticada."""

    __slots__ = ("code", "autenticado")

    def __init__(self, code: AuthorizationCode, autenticado: Autenticado) -> None:
        self.code = code
        self.autenticado = autenticado


class WinthorAuthProvider(OAuthProvider):
    def __init__(self) -> None:
        conf = configuracao()
        if conf.public_url is None:
            raise ValueError("WINTHOR_PUBLIC_URL é obrigatório para o modo remoto (OAuth).")
        super().__init__(
            base_url=conf.public_url,
            # Clientes MCP se registram sozinhos (Dynamic Client Registration).
            client_registration_options=ClientRegistrationOptions(enabled=True),
        )
        self._clientes: dict[str, OAuthClientInformationFull] = {}
        self._pendencias: dict[str, _Pendencia] = {}
        self._codes: dict[str, _CodeAutenticado] = {}
        self._tokens: dict[str, AccessToken] = {}

    # --- registro de cliente (DCR) ----------------------------------------
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clientes.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if client_info.client_id is None:
            raise ValueError("client_id é obrigatório no registro do cliente.")
        self._clientes[client_info.client_id] = client_info

    # --- autorização: manda para o login ----------------------------------
    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if client.client_id not in self._clientes:
            raise AuthorizeError(
                error="unauthorized_client",
                error_description="Cliente não registrado.",
            )
        pedido = secrets.token_urlsafe(24)
        self._pendencias[pedido] = _Pendencia(client, params)
        # O navegador vai para a tela de login própria; o código só nasce depois
        # que a pessoa autentica com sucesso.
        return f"{configuracao().public_url}/winthor/login?pedido={pedido}"

    async def _emitir_code(self, pendencia: _Pendencia, autenticado: Autenticado) -> str:
        params = pendencia.params
        valor = f"wc_{secrets.token_hex(24)}"
        code = AuthorizationCode(
            code=valor,
            client_id=pendencia.client.client_id or "",
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            scopes=params.scopes or [],
            expires_at=time.time() + _VALIDADE_CODE_SEGUNDOS,
            code_challenge=params.code_challenge,
            resource=params.resource,
        )
        self._codes[valor] = _CodeAutenticado(code, autenticado)
        return construct_redirect_uri(str(params.redirect_uri), code=valor, state=params.state)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        entrada = self._codes.get(authorization_code)
        if entrada is None or entrada.code.client_id != client.client_id:
            return None
        if entrada.code.expires_at < time.time():
            self._codes.pop(authorization_code, None)
            return None
        return entrada.code

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        entrada = self._codes.pop(authorization_code.code, None)
        if entrada is None:
            raise TokenError("invalid_grant", "Código inválido ou já usado.")

        token = f"wt_{secrets.token_hex(32)}"
        vida = configuracao().sessao_horas * 3600
        expira = int(time.time() + vida)
        self._tokens[token] = AccessToken(
            token=token,
            client_id=client.client_id or "",
            scopes=authorization_code.scopes,
            expires_at=expira,
            subject=str(entrada.autenticado.identidade.matricula),
        )
        # O token de acesso é a chave da sessão: identidade e credencial WTA
        # vivem aqui, em memória, e somem quando o token expira ou é revogado.
        aut = entrada.autenticado
        sessao.abrir(token, aut.identidade, aut.usuario_wta, aut.credencial_wta, vida)
        return OAuthToken(
            access_token=token,
            token_type="Bearer",
            expires_in=vida,
            scope=" ".join(authorization_code.scopes),
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        guardado = self._tokens.get(token)
        if guardado is None:
            return None
        if guardado.expires_at is not None and guardado.expires_at < time.time():
            self._tokens.pop(token, None)
            sessao.encerrar(token)
            return None
        return guardado

    async def revoke_token(self, token: AccessToken) -> None:
        self._tokens.pop(token.token, None)
        sessao.encerrar(token.token)

    # OAuth 2.1 sem refresh: a sessão renova o token do WTA por baixo, e quando
    # a própria sessão expira a pessoa faz login de novo.
    async def load_refresh_token(self, client: Any, refresh_token: str) -> None:
        return None

    async def exchange_refresh_token(self, *args: Any, **kwargs: Any) -> OAuthToken:
        raise TokenError("unsupported_grant_type", "Este servidor não usa refresh token.")

    # --- rotas próprias: a tela de login ----------------------------------
    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        rotas = super().get_routes(mcp_path)
        rotas.append(Route("/winthor/login", self._get_login, methods=["GET"]))
        rotas.append(Route("/winthor/login", self._post_login, methods=["POST"]))
        return rotas

    async def _get_login(self, request: Request) -> Response:
        pedido = request.query_params.get("pedido", "")
        if pedido not in self._pendencias:
            return HTMLResponse("Pedido de login inválido ou expirado.", status_code=400)
        return HTMLResponse(pagina_login(pedido))

    async def _post_login(self, request: Request) -> Response:
        form = await request.form()
        pedido = str(form.get("pedido", ""))
        matricula = str(form.get("matricula", "")).strip()
        senha = str(form.get("senha", ""))
        pendencia = self._pendencias.get(pedido)
        if pendencia is None:
            return HTMLResponse("Pedido de login inválido ou expirado.", status_code=400)

        try:
            autenticado = await autenticar_para_sessao(matricula, senha)
        except SemComoValidar as erro:
            return HTMLResponse(pagina_login(pedido, str(erro)), status_code=200)
        if autenticado is None:
            return HTMLResponse(
                pagina_login(pedido, "Matrícula ou senha incorreta."), status_code=200
            )

        self._pendencias.pop(pedido, None)
        destino = await self._emitir_code(pendencia, autenticado)
        return RedirectResponse(destino, status_code=303)
