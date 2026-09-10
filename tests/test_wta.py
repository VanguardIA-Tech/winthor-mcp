"""O cliente do WinThor Anywhere contra um WTA simulado.

Nada aqui toca Oracle: a camada HTTP é testável sozinha, e amarrar o teste ao
banco do cliente tornaria a suíte inexecutável fora da rede dele.
"""

from __future__ import annotations

import hashlib

import httpx
import pytest
import respx

from winthor_mcp.configuracao import configuracao
from winthor_mcp.wta import cliente, servicos

URL = "http://wta.teste:8080"
LOGIN = f"{URL}/winthor/autenticacao/v1/login"


@pytest.fixture(autouse=True)
def ambiente(monkeypatch: pytest.MonkeyPatch):
    """Configuração e estado de módulo zerados a cada teste.

    O token, o client e a sondagem vivem em variáveis de módulo — sem limpar,
    o teste do relogin herdaria o token do teste anterior e passaria por acaso.
    """
    for chave, valor in {
        "WINTHOR_DB_HOST": "oracle.teste",
        "WINTHOR_DB_SERVICE": "winthor",
        "WINTHOR_DB_USER": "leitor",
        "WINTHOR_DB_PASSWORD": "segredo",
        "WINTHOR_WTA_URL": URL,
        "WINTHOR_USUARIO": "1234",
        "WINTHOR_SENHA": "minhasenha",
        "WINTHOR_WTA_TIMEOUT_SEGUNDOS": "5",
    }.items():
        monkeypatch.setenv(chave, valor)
    configuracao.cache_clear()
    cliente._cliente = None
    cliente._token = None
    servicos.esquecer()
    yield
    cliente._cliente = None
    cliente._token = None
    servicos.esquecer()
    configuracao.cache_clear()


def md5_maiusculo(senha: str) -> str:
    return hashlib.md5(senha.upper().encode(), usedforsecurity=False).hexdigest().upper()


@respx.mock
async def test_entrar_devolve_token_e_manda_md5_maiusculo():
    rota = respx.post(LOGIN).mock(return_value=httpx.Response(200, json={"accessToken": "tok-1"}))

    assert await cliente.entrar("1234", "MinhaSenha") == "tok-1"

    enviado = rota.calls.last.request
    assert b'"senha":"' + md5_maiusculo("MinhaSenha").encode() in enviado.content
    # A senha em claro não pode aparecer em lugar nenhum da requisição.
    assert b"MinhaSenha" not in enviado.content


@respx.mock
async def test_entrar_com_credencial_recusada_devolve_none():
    respx.post(LOGIN).mock(return_value=httpx.Response(401, json={"erro": "invalido"}))

    assert await cliente.entrar("1234", "errada") is None


@respx.mock
async def test_chamar_leva_bearer_e_nao_poe_token_na_url():
    rota = respx.get(f"{URL}/api/branch/v1/filiais").mock(
        return_value=httpx.Response(200, json={"filiais": [{"codigo": 1}]})
    )

    resultado = await cliente.chamar("GET", "/api/branch/v1/filiais", token="tok-1")

    assert resultado.ok
    assert resultado.dados == {"filiais": [{"codigo": 1}]}
    pedido = rota.calls.last.request
    assert pedido.headers["Authorization"] == "Bearer tok-1"
    assert "tok-1" not in str(pedido.url)


@respx.mock
async def test_chamar_com_erro_devolve_resultado_com_codigo_http():
    respx.get(f"{URL}/api/branch/v1/filiais").mock(return_value=httpx.Response(500, text="boom"))

    resultado = await cliente.chamar("GET", "/api/branch/v1/filiais", token="tok-1")

    assert not resultado.ok
    assert [erro.codigo for erro in resultado.erros] == ["500"]


@respx.mock
async def test_401_dispara_relogin_e_repete_a_chamada():
    login = respx.post(LOGIN).mock(
        side_effect=[
            httpx.Response(200, json={"accessToken": "velho"}),
            httpx.Response(200, json={"accessToken": "novo"}),
        ]
    )
    dados = respx.get(f"{URL}/api/wholesale/v1/pedidos").mock(
        side_effect=[
            httpx.Response(401, json={"erro": "token expirado"}),
            httpx.Response(200, json={"pedidos": []}),
        ]
    )

    resultado = await cliente.requisitar("GET", "/api/wholesale/v1/pedidos")

    assert resultado.ok
    assert resultado.dados == {"pedidos": []}
    assert login.call_count == 2
    assert dados.call_count == 2
    assert dados.calls[0].request.headers["Authorization"] == "Bearer velho"
    assert dados.calls[1].request.headers["Authorization"] == "Bearer novo"
    assert cliente._token == "novo"


@respx.mock
async def test_401_persistente_nao_entra_em_loop():
    respx.post(LOGIN).mock(return_value=httpx.Response(200, json={"accessToken": "tok"}))
    dados = respx.get(f"{URL}/api/wholesale/v1/pedidos").mock(
        return_value=httpx.Response(401, json={"erro": "sem permissao"})
    )

    resultado = await cliente.requisitar("GET", "/api/wholesale/v1/pedidos")

    assert not resultado.ok
    # Duas tentativas e para: relogar de novo só multiplicaria login no ERP.
    assert dados.call_count == 2


@respx.mock
async def test_disponiveis_traz_so_quem_respondeu():
    ativos = {"/api/wholesale/v1", "/api/branch/v1", "/winthor/tributacao/v0"}
    for servico in servicos.CATALOGO:
        alvo = respx.get(f"{URL}{servico.prefixo}/health")
        if servico.prefixo in ativos:
            alvo.mock(return_value=httpx.Response(200, json={"status": "Aplicação ativa"}))
        else:
            alvo.mock(return_value=httpx.Response(404))

    encontrados = await servicos.disponiveis()

    assert {servico.prefixo for servico in encontrados} == ativos
    assert all(servico.ativo for servico in encontrados)
    assert all(servico.dominio for servico in encontrados)


@respx.mock
async def test_disponiveis_ignora_servico_que_nem_conecta_e_usa_cache():
    # A rota específica vem antes: o respx casa na ordem de registro.
    respx.get(f"{URL}/api/branch/v1/health").mock(return_value=httpx.Response(200))
    fora = respx.get(url__regex=rf"{URL}/.*/health").mock(
        side_effect=httpx.ConnectError("recusado")
    )

    primeira = await servicos.disponiveis()
    segunda = await servicos.disponiveis()

    assert [servico.nome for servico in primeira] == ["winthor-filial"]
    assert segunda == primeira
    # A instalação não muda no meio da conversa: a segunda leitura é do cache.
    assert fora.call_count == len(servicos.CATALOGO) - 1


async def test_disponiveis_sem_wta_nao_toca_a_rede(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("WINTHOR_WTA_URL")
    configuracao.cache_clear()

    with respx.mock:  # qualquer requisição aqui viraria erro de rota não casada
        assert await servicos.disponiveis() == []


@respx.mock
async def test_rotas_publicadas_ausentes_quando_fulfillment_nao_existe():
    respx.get(url__regex=rf"{URL}/.*/health").mock(return_value=httpx.Response(404))

    assert await servicos.rotas_publicadas() is None


@respx.mock
async def test_rotas_publicadas_consulta_o_fulfillment_quando_ele_esta_de_pe():
    respx.get(f"{URL}{servicos.PREFIXO_FULFILLMENT}/health").mock(
        return_value=httpx.Response(200, json={"status": "Aplicação ativa"})
    )
    respx.get(url__regex=rf"{URL}/.*/health").mock(return_value=httpx.Response(404))
    respx.post(LOGIN).mock(return_value=httpx.Response(200, json={"accessToken": "tok"}))
    rota = respx.get(f"{URL}{servicos.PREFIXO_FULFILLMENT}/layout/resolverUrlsRotasWta").mock(
        return_value=httpx.Response(200, json={"rotas": [{"rota": 316, "url": f"{URL}/x"}]})
    )

    publicadas = await servicos.rotas_publicadas()

    assert publicadas == {"rotas": [{"rota": 316, "url": f"{URL}/x"}]}
    assert rota.calls.last.request.url.params["integracao"] == "pdvsync"


@respx.mock
async def test_sair_encerra_a_sessao_e_engole_falha():
    logout = respx.get(f"{URL}/winthor/autenticacao/v1/logout").mock(
        return_value=httpx.Response(500)
    )

    await cliente.sair("tok-1")

    assert logout.calls.last.request.headers["Authorization"] == "Bearer tok-1"
