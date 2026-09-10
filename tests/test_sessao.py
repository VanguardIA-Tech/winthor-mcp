"""A sessão por-usuário: identidade, TTL e renovação do token WTA."""

import pytest

from winthor_mcp.modelos import Identidade
from winthor_mcp.sessao import usuario


@pytest.fixture(autouse=True)
def _limpa():
    usuario._limpar_tudo()
    yield
    usuario._limpar_tudo()


def _identidade(matricula: int = 22) -> Identidade:
    return Identidade(matricula=matricula, nome="FULANO", situacao="A")


def test_abrir_e_obter_devolve_a_mesma_sessao():
    usuario.abrir("s1", _identidade(), "USUARIO.X", "MD5", vida_segundos=3600)
    s = usuario.obter("s1")
    assert s is not None and s.matricula == 22


def test_sessao_expirada_some_ao_ser_procurada():
    usuario.abrir("s1", _identidade(), "USUARIO.X", "MD5", vida_segundos=-1)
    assert usuario.obter("s1") is None


def test_encerrar_esquece_a_credencial():
    usuario.abrir("s1", _identidade(), "USUARIO.X", "MD5", vida_segundos=3600)
    usuario.encerrar("s1")
    assert usuario.obter("s1") is None


async def test_token_wta_none_sem_wta_configurado(monkeypatch):
    # Sem WTA, não há token de sessão a devolver.
    from winthor_mcp import configuracao as cfg

    class _Conf:
        tem_wta = False

    monkeypatch.setattr(cfg, "configuracao", lambda: _Conf())
    usuario.abrir("s1", _identidade(), "USUARIO.X", "MD5", vida_segundos=3600)
    assert await usuario.token_wta_da_sessao("s1") is None


async def test_token_wta_renova_com_a_credencial(monkeypatch):
    from winthor_mcp import configuracao as cfg
    from winthor_mcp.wta import cliente

    class _Conf:
        tem_wta = True

    monkeypatch.setattr(cfg, "configuracao", lambda: _Conf())

    chamadas = []

    async def _fake_entrar(mat, md5):
        chamadas.append((mat, md5))
        return "tok-wta"

    monkeypatch.setattr(cliente, "entrar_com_md5", _fake_entrar)
    usuario.abrir("s1", _identidade(22), "FULANO.X", "MD5DAPESSOA", vida_segundos=3600)

    # primeira vez: reloga; segunda: reaproveita o token ainda válido
    assert await usuario.token_wta_da_sessao("s1") == "tok-wta"
    assert await usuario.token_wta_da_sessao("s1") == "tok-wta"
    assert chamadas == [("FULANO.X", "MD5DAPESSOA")]


async def test_token_wta_none_sem_credencial(monkeypatch):
    # Login validado offline (SENHABD) não guarda credencial WTA: sem material
    # para relogar, não há token — a pessoa precisa autenticar de novo.
    from winthor_mcp import configuracao as cfg

    class _Conf:
        tem_wta = True

    monkeypatch.setattr(cfg, "configuracao", lambda: _Conf())
    usuario.abrir("s1", _identidade(), None, None, vida_segundos=3600)
    assert await usuario.token_wta_da_sessao("s1") is None
