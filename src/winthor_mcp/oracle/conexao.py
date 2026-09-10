"""A única porta de entrada para o Oracle do WinThor.

Driver thin: nada de Instant Client, nada de wallet, nada de biblioteca nativa
dentro da imagem. Uma Autonomous atende por TLS de uma via e uma instalação
local atende em claro; os dois casos funcionam com o mesmo Easy Connect.

Todo o resto do projeto lê o ERP por `consultar`. Ninguém abre cursor sozinho.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import oracledb

from winthor_mcp.configuracao import configuracao

# Uma consulta que passa disso não vai ser esperada por ninguém, e deixá-la
# correndo só segura sessão aberta num ERP em produção.
TIMEOUT_CONSULTA_MS = 30_000
# O catálogo abre várias consultas juntas na primeira pergunta; reaproveitar
# sessão vale mais que um handshake por chamada.
POOL_MAXIMO = 8
POOL_ESPERA_MS = 8_000

_pool: Any | None = None
_trava = asyncio.Lock()


async def _obter_pool() -> Any:
    global _pool
    async with _trava:
        if _pool is not None:
            return _pool
        conf = configuracao()
        _pool = oracledb.create_pool_async(
            user=conf.db_user,
            password=conf.db_password.get_secret_value(),
            dsn=conf.dsn,
            min=0,
            max=POOL_MAXIMO,
            increment=1,
            getmode=oracledb.POOL_GETMODE_TIMEDWAIT,
            wait_timeout=POOL_ESPERA_MS,
        )
        return _pool


def portavel(valor: Any) -> Any:
    """Estreita um valor do Oracle para algo que JSON carrega.

    `Decimal` vira string, não float: valor de pedido é dinheiro, e float move
    os centavos de lugar sem avisar. Quem consome converte com a precisão que
    precisa.
    """
    match valor:
        case datetime() | date():
            return valor.isoformat()
        case Decimal():
            return str(valor)
        case bytes():
            return valor.hex()
        case _:
            return valor


async def consultar(sql: str, /, limite: int = 500, **parametros: Any) -> list[dict[str, Any]]:
    """Roda um SELECT e devolve linhas como dicionários já portáveis.

    Só leitura: o servidor nunca monta INSERT ou UPDATE contra as tabelas do
    ERP. Escrita é assunto do WinThor Anywhere, que aplica as regras de
    negócio da rotina — reproduzi-las em SQL seria inventar uma segunda
    versão do ERP.
    """
    pool = await _obter_pool()
    async with pool.acquire() as conexao:
        conexao.call_timeout = TIMEOUT_CONSULTA_MS
        cursor = conexao.cursor()
        await cursor.execute(sql, **parametros)
        linhas = await cursor.fetchmany(limite)
        colunas = [descricao[0].lower() for descricao in cursor.description]
    return [
        {coluna: portavel(valor) for coluna, valor in zip(colunas, linha, strict=True)}
        for linha in linhas
    ]


async def testar() -> bool:
    """O ERP está alcançável com esta credencial? Usado pelo diagnóstico."""
    try:
        await consultar("select 1 from dual", limite=1)
    except Exception:
        return False
    return True


async def encerrar() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
