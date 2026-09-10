"""O canal HTTP com o WinThor Anywhere.

O WTA é uma plataforma OSGi (Apache Karaf + Pax Web): os ~120 "microserviços"
são bundles dentro da mesma JVM, servidos pela mesma porta. Por isso existe um
único `host:porta` aqui e nenhuma descoberta de serviço por porta — o que muda
de um serviço para outro é só o prefixo do caminho.

A autenticação é por token Bearer e não há endpoint de refresh documentado. O
único caminho de renovação é relogar. Como a validade observada não é
contrato, este módulo não conta tempo: reage ao 401 que o servidor mandar.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

import httpx

from winthor_mcp.configuracao import configuracao
from winthor_mcp.modelos import Erro, Resultado

CAMINHO_LOGIN = "/winthor/autenticacao/v1/login"
CAMINHO_LOGOUT = "/winthor/autenticacao/v1/logout"

_cliente: httpx.AsyncClient | None = None
_token: str | None = None
_trava = asyncio.Lock()


def hash_senha(senha: str) -> str:
    """A senha viaja como MD5 do texto em maiúsculas, e o hash também sobe em
    maiúsculas — é o que a TOTVS documenta para o WinThor Anywhere.

    Exposto porque a sessão guarda este hash (não a senha) para relogar quando
    o token de 4h vence.
    """
    return hashlib.md5(senha.upper().encode("utf-8"), usedforsecurity=False).hexdigest().upper()


async def cliente() -> httpx.AsyncClient:
    """O `AsyncClient` compartilhado, criado na primeira chamada.

    Tardio de propósito: no import a configuração ainda pode não existir, e um
    client por requisição jogaria fora o pool de conexões a cada chamada.
    """
    global _cliente
    async with _trava:
        if _cliente is None or _cliente.is_closed:
            conf = configuracao()
            _cliente = httpx.AsyncClient(
                base_url=conf.wta_url or "",
                timeout=conf.wta_timeout_segundos,
                verify=conf.wta_verify_tls,
            )
        return _cliente


async def entrar(usuario: str, senha: str) -> str | None:
    """Faz login e devolve o token, ou `None` se o WTA recusou.

    Credencial errada não é exceção: é resposta. Quem chama decide o que
    fazer com o `None` sem precisar capturar nada.
    """
    return await entrar_com_md5(usuario, hash_senha(senha))


async def entrar_com_md5(usuario: str, md5_senha: str) -> str | None:
    """Igual a `entrar`, mas recebe o MD5 já calculado — é o que a sessão tem
    guardado para relogar sem nunca ter a senha em claro."""
    http = await cliente()
    try:
        resposta = await http.post(
            CAMINHO_LOGIN,
            json={"login": usuario, "senha": md5_senha},
        )
    except httpx.HTTPError:
        return None
    if resposta.status_code != httpx.codes.OK:
        return None
    try:
        return resposta.json().get("accessToken")
    except ValueError:
        return None


async def sair(token: str) -> None:
    """Encerra a sessão no servidor. Falhar aqui não interessa a ninguém: o
    token já foi descartado do lado de cá."""
    http = await cliente()
    try:
        await http.get(CAMINHO_LOGOUT, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError:
        return


async def chamar(
    metodo: str,
    caminho: str,
    *,
    token: str,
    json: Any = None,
    params: dict[str, Any] | None = None,
) -> Resultado:
    """Uma requisição ao WTA com o token já no cabeçalho.

    O token vai em `Authorization` e em lugar nenhum mais: URL e query string
    aparecem em log de proxy e em histórico de servidor.
    """
    http = await cliente()
    try:
        resposta = await http.request(
            metodo.upper(),
            caminho,
            headers={"Authorization": f"Bearer {token}"},
            json=json,
            params=params,
        )
    except httpx.HTTPError as erro:
        return Resultado(
            ok=False,
            erros=[Erro(codigo="rede", mensagem=f"{type(erro).__name__} em {caminho}")],
        )
    return _resultado(resposta, caminho)


def _resultado(resposta: httpx.Response, caminho: str) -> Resultado:
    try:
        corpo = resposta.json()
    except ValueError:
        corpo = resposta.text or None
    if resposta.is_success:
        return Resultado(ok=True, dados=corpo if isinstance(corpo, dict | list) else None)
    return Resultado(
        ok=False,
        erros=[
            Erro(
                codigo=str(resposta.status_code),
                mensagem=f"{resposta.status_code} em {caminho}",
            )
        ],
    )


async def token_sessao(*, renovar: bool = False) -> str | None:
    """O token da sessão do processo, logando quando não houver um.

    A credencial é a de quem instalou o servidor — nunca chega por parâmetro
    de ferramenta.
    """
    global _token
    conf = configuracao()
    usuario, senha = conf.usuario, conf.senha
    if not (conf.tem_wta and usuario and senha):
        return None
    async with _trava:
        if _token is not None and not renovar:
            return _token
        _token = None
    novo = await entrar(usuario, senha.get_secret_value())
    async with _trava:
        _token = novo
    return novo


async def requisitar(
    metodo: str,
    caminho: str,
    *,
    json: Any = None,
    params: dict[str, Any] | None = None,
) -> Resultado:
    """Chama o WTA usando a sessão do processo, relogando uma vez em 401.

    Uma vez só: se o servidor recusa o token recém-emitido, o problema é a
    credencial ou a permissão, e insistir só multiplica login no ERP.
    """
    token = await token_sessao()
    if token is None:
        return Resultado(
            ok=False,
            erros=[
                Erro(
                    codigo="sem_sessao",
                    mensagem="WinThor Anywhere sem URL ou sem credencial",
                )
            ],
        )
    resultado = await chamar(metodo, caminho, token=token, json=json, params=params)
    if not _expirou(resultado):
        return resultado
    token = await token_sessao(renovar=True)
    if token is None:
        return resultado
    return await chamar(metodo, caminho, token=token, json=json, params=params)


def _expirou(resultado: Resultado) -> bool:
    return not resultado.ok and any(erro.codigo == "401" for erro in resultado.erros)


async def encerrar() -> None:
    """Devolve o processo ao estado de antes: sessão fechada, client fechado."""
    global _cliente, _token
    token, _token = _token, None
    if token is not None:
        await sair(token)
    if _cliente is not None:
        await _cliente.aclose()
        _cliente = None
