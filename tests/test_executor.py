"""O executor de operações WTA: montagem de caminho, validação e chamada."""

import httpx
import pytest
import respx

from winthor_mcp.wta import cliente
from winthor_mcp.wta.executor import executar_operacao, montar, validar
from winthor_mcp.wta.operacoes import operacao


@pytest.fixture(autouse=True)
def _wta(monkeypatch):
    monkeypatch.setenv("WINTHOR_DB_HOST", "x")
    monkeypatch.setenv("WINTHOR_DB_SERVICE", "y")
    monkeypatch.setenv("WINTHOR_DB_USER", "Z")
    monkeypatch.setenv("WINTHOR_DB_PASSWORD", "p")
    monkeypatch.setenv("WINTHOR_WTA_URL", "https://wta.exemplo")
    import winthor_mcp.configuracao as cfg

    cfg.configuracao.cache_clear()
    yield
    cfg.configuracao.cache_clear()


def test_parametro_de_rota_entra_no_caminho():
    op = operacao("consultar_estoque")
    caminho, query, corpo = montar(op, {"filial": 1, "produto": 500})
    assert caminho == "/api/stock-vtex/v1/available/1/500"
    assert query == {}


def test_parametro_comum_vira_query_e_desconhecido_e_ignorado():
    op = operacao("listar_pedidos")
    caminho, query, corpo = montar(op, {"branchId": 1, "page": 2, "inventado": "x"})
    assert caminho == "/api/wholesale/v1/orders/list"
    assert query == {"branchId": 1, "page": 2}


def test_validar_cobra_obrigatorio():
    op = operacao("buscar_cliente")
    assert validar(op, {}) != []
    assert validar(op, {"customerId": 7}) == []


async def test_executar_manda_bearer_e_devolve_dados():
    op = operacao("listar_clientes")
    with respx.mock(base_url="https://wta.exemplo") as mock:
        rota = mock.get("/api/wholesale/v1/customer/list").mock(
            return_value=httpx.Response(200, json={"items": [{"customerId": 1}]})
        )
        resultado = await executar_operacao(op, {"page": 1}, "tok-da-camila")
    assert resultado.ok
    assert resultado.dados == {"items": [{"customerId": 1}]}
    assert rota.calls.last.request.headers["Authorization"] == "Bearer tok-da-camila"
    await cliente.encerrar()


async def test_operacao_de_escrita_manda_corpo_json():
    op = operacao("cadastrar_cliente")
    with respx.mock(base_url="https://wta.exemplo") as mock:
        rota = mock.post("/api/wholesale/v1/customer/").mock(
            return_value=httpx.Response(201, json={"customerId": 99})
        )
        resultado = await executar_operacao(op, {"dados": {"name": "ACME"}}, "tok")
    assert resultado.ok
    import json as _json

    assert _json.loads(rota.calls.last.request.content) == {"name": "ACME"}
    await cliente.encerrar()


async def test_executar_reprova_sem_parametro_obrigatorio():
    op = operacao("buscar_cliente")
    resultado = await executar_operacao(op, {}, "tok")
    assert not resultado.ok
    assert resultado.erros[0].codigo == "winthor.operacao.parametro_faltando"
