"""Quais serviços do WinThor Anywhere este cliente tem instalados.

A rotina 801 instala bundles OSGi e grava um ponto de restauração a cada
atualização. O inventário desse ponto fica no Oracle, então dá para saber o
que existe sem falar com o WTA — útil quando ele está fora do ar, e útil para
casar rotina com serviço antes de qualquer chamada HTTP.

Numa base real: 484 pontos de restauração, o mais recente com 120 serviços.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from winthor_mcp.configuracao import configuracao
from winthor_mcp.oracle import consultar

_SCHEMA_VALIDO = re.compile(r"^[A-Z][A-Z0-9_$#]*$")

# Os bundles de rotina nomeiam a rotina que servem: winthor-fer-0807,
# winthor-exp-3804, winthor-fin-1297, winthor-ven-1420, winthor-fer-802.
# É daí que sai o vínculo rotina -> serviço, sem lista fixa neste projeto.
_CODIGO_NO_NOME = re.compile(r"-(?:fer|exp|fin|ven|asd|integracao|rot)?-?(\d{3,4})(?:-|$)")

_CONSULTA = """
SELECT s.NOME, s.VERSAO, s.REPOSITORIO
  FROM {schema}.PCWTASNAPSHOTSTATE s
 WHERE s.SNAPSHOTID = (
        SELECT ID FROM (
            SELECT ID FROM {schema}.PCWTASNAPSHOT ORDER BY DATACRIACAO DESC
        ) WHERE ROWNUM = 1)
 ORDER BY s.NOME
"""


@dataclass(frozen=True, slots=True)
class ServicoInstalado:
    nome: str
    versao: str | None
    rotina: int | None
    """Rotina que este bundle serve, quando o nome diz qual."""


def _schema() -> str:
    schema = configuracao().db_schema
    if not _SCHEMA_VALIDO.match(schema):
        raise ValueError(f"schema inválido para consulta: {schema!r}")
    return schema


def _rotina_do_nome(nome: str) -> int | None:
    achado = _CODIGO_NO_NOME.search(nome)
    if achado is None:
        return None
    codigo = int(achado.group(1))
    # "winthor-jackson 2.9.5" e afins não são rotina; a faixa real do WinThor
    # vai de 100 a 9999, e abaixo de 100 é quase sempre número de versão.
    return codigo if 100 <= codigo <= 9999 else None


_cache: tuple[float, list[ServicoInstalado]] | None = None


async def instalados(*, forcar: bool = False) -> list[ServicoInstalado]:
    """O inventário do último ponto de restauração da 801.

    Devolve lista vazia quando as tabelas do WTA não existem — uma instalação
    sem WinThor Anywhere é um cenário normal, não um erro.
    """
    global _cache
    agora = time.monotonic()
    if not forcar and _cache is not None and agora - _cache[0] < configuracao().cache_segundos:
        return _cache[1]
    try:
        linhas = await consultar(_CONSULTA.format(schema=_schema()), limite=500)
    except Exception:
        _cache = (agora, [])
        return []
    servicos = [
        ServicoInstalado(
            nome=str(linha["nome"]),
            versao=str(linha["versao"]) if linha.get("versao") else None,
            rotina=_rotina_do_nome(str(linha["nome"])),
        )
        for linha in linhas
        if linha.get("nome")
    ]
    _cache = (agora, servicos)
    return servicos


def invalidar() -> None:
    global _cache
    _cache = None
