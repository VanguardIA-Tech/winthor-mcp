"""O catálogo de rotinas, lido do WinThor do próprio cliente.

Não existe lista de rotinas escrita neste projeto, e isso é deliberado: cada
instalação tem as suas. A 316 de fábrica e a 9002 que o analista da casa
escreveu na semana passada saem da mesma consulta a PCROTINA e chegam aqui
indistinguíveis. Uma lista embutida envelheceria no primeiro cliente.

O catálogo inteiro cabe em memória com folga, então ele é lido de uma vez e
servido de lá; `rotina` e `buscar` nunca voltam ao Oracle.
"""

from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any

from winthor_mcp.configuracao import configuracao
from winthor_mcp.modelos import Modulo, Rotina, Submodulo
from winthor_mcp.oracle import consultar

# Identificador Oracle sem aspas: o schema entra no SQL por interpolação porque
# nome de objeto não aceita bind, e a config vem do ambiente de quem instalou.
_IDENTIFICADOR = re.compile(r"^[A-Z][A-Z0-9_$#]*$")

# PCROTINA passa de 1600 linhas nesta base e cresce com o que a casa escreveu.
# O limite padrão de `consultar` cortaria o catálogo sem reclamar, e um catálogo
# pela metade é pior que um erro.
_LIMITE_ROTINAS = 20_000
_LIMITE_MENU = 5_000


@dataclass(frozen=True, slots=True)
class _Catalogo:
    modulos: list[Modulo]
    submodulos: list[Submodulo]
    rotinas: list[Rotina]
    por_codigo: dict[int, Rotina]
    carregado_em: float


_catalogo: _Catalogo | None = None
_trava = asyncio.Lock()


def _schema() -> str:
    esquema = configuracao().db_schema
    if not _IDENTIFICADOR.match(esquema):
        raise ValueError(f"schema inválido para interpolação em SQL: {esquema!r}")
    return esquema


def _normalizar(texto: str) -> str:
    """Compara como quem digita: sem acento, sem caixa, sem cedilha.

    Ninguém procura "Manutenção" com o til no lugar certo, e metade das
    descrições do ERP foi cadastrada em caixa alta sem acento nenhum.
    """
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c)).casefold()


def _texto(valor: Any) -> str | None:
    """VARCHAR2 do WinThor vem com espaço de sobra e vazio vale como ausente."""
    if valor is None:
        return None
    limpo = str(valor).strip()
    return limpo or None


def _sim(valor: Any) -> bool:
    """Flag do WinThor: 'S' é sim, 'N' e NULL são não."""
    return _texto(valor) == "S"


def _para_rotina(linha: dict[str, Any]) -> Rotina:
    # DATE do Oracle sempre carrega hora, e `portavel` já entregou o ISO
    # completo. O modelo quer o dia: sem cortar, uma rotina usada às 14h32
    # derruba a carga inteira na validação.
    uso = linha["dtultutilizacao"]
    return Rotina(
        codigo=int(linha["codigo"]),
        nome=_texto(linha["nomerotina"]) or f"Rotina {linha['codigo']}",
        modulo=linha["codmodulo"],
        modulo_nome=_texto(linha["modulo"]),
        submodulo=linha["codsubmodulo"],
        submodulo_nome=_texto(linha["submodulo"]),
        executavel=_texto(linha["rotina"]),
        # VERSAOEXEATUAL é a versão do binário que roda; VERSAOCOMPLETA descreve
        # o pacote de instalação e não ajuda quem pergunta "que versão é essa?".
        versao=_texto(linha["versaoexeatual"]),
        tela_web=_sim(linha["rotinaweb"]),
        no_menu=_sim(linha["exibirmenu"]),
        usos=linha["qtutilizacao"],
        ultimo_uso=str(uso)[:10] if uso else None,
    )


async def _carregar() -> _Catalogo:
    esquema = _schema()

    linhas_modulos, linhas_submodulos, linhas_rotinas = await asyncio.gather(
        consultar(
            f"select codmodulo, modulo from {esquema}.pcmodulo order by codmodulo",
            limite=_LIMITE_MENU,
        ),
        consultar(
            f"""select codmodulo, codsubmodulo, submodulo
                  from {esquema}.pcsubmodulo
                 order by codmodulo, codsubmodulo""",
            limite=_LIMITE_MENU,
        ),
        consultar(
            # CODSUBMODULO se repete entre módulos, então a junção precisa das
            # duas colunas — só por CODSUBMODULO o nome sai do módulo errado.
            f"""select r.codigo, r.nomerotina, r.rotina,
                       r.codmodulo, m.modulo,
                       r.codsubmodulo, s.submodulo,
                       r.exibirmenu, r.rotinaweb, r.versaoexeatual,
                       r.qtutilizacao, r.dtultutilizacao
                  from {esquema}.pcrotina r
                  left join {esquema}.pcmodulo m
                         on m.codmodulo = r.codmodulo
                  left join {esquema}.pcsubmodulo s
                         on s.codmodulo = r.codmodulo
                        and s.codsubmodulo = r.codsubmodulo
                 order by r.codigo""",
            limite=_LIMITE_ROTINAS,
        ),
    )

    rotinas_lidas = [_para_rotina(linha) for linha in linhas_rotinas]
    return _Catalogo(
        modulos=[
            Modulo(codigo=linha["codmodulo"], nome=_texto(linha["modulo"]) or "")
            for linha in linhas_modulos
        ],
        submodulos=[
            Submodulo(
                codigo_modulo=linha["codmodulo"],
                codigo=linha["codsubmodulo"],
                nome=_texto(linha["submodulo"]) or "",
            )
            for linha in linhas_submodulos
        ],
        rotinas=rotinas_lidas,
        por_codigo={r.codigo: r for r in rotinas_lidas},
        carregado_em=time.monotonic(),
    )


async def _obter() -> _Catalogo:
    global _catalogo
    ttl = configuracao().cache_segundos
    atual = _catalogo
    if atual is not None and time.monotonic() - atual.carregado_em < ttl:
        return atual
    async with _trava:
        # Várias tools podem ter passado pela verificação acima antes de a
        # primeira terminar de ler; sem reconferir aqui, cada uma repetiria as
        # três consultas contra o ERP.
        atual = _catalogo
        if atual is not None and time.monotonic() - atual.carregado_em < ttl:
            return atual
        _catalogo = await _carregar()
        return _catalogo


async def modulos() -> list[Modulo]:
    return (await _obter()).modulos


async def submodulos() -> list[Submodulo]:
    return (await _obter()).submodulos


async def rotinas() -> list[Rotina]:
    return (await _obter()).rotinas


async def rotina(codigo: int) -> Rotina | None:
    return (await _obter()).por_codigo.get(codigo)


async def buscar(
    texto: str | None = None,
    modulo: int | None = None,
    apenas_menu: bool = False,
) -> list[Rotina]:
    """Filtra o catálogo em memória — mil e setecentas linhas não pedem SQL.

    O texto casa também contra o código porque é assim que a pessoa fala da
    rotina: ela pede "a 316", não "Cadastro de Produto".
    """
    catalogo = await _obter()
    encontradas = catalogo.rotinas

    if modulo is not None:
        encontradas = [r for r in encontradas if r.modulo == modulo]
    if apenas_menu:
        encontradas = [r for r in encontradas if r.no_menu]
    if texto:
        alvo = _normalizar(texto.strip())
        encontradas = [
            r
            for r in encontradas
            if alvo in _normalizar(r.nome)
            or alvo in str(r.codigo)
            or (r.executavel is not None and alvo in _normalizar(r.executavel))
        ]
    return encontradas


def invalidar() -> None:
    """Descarta o catálogo. Quem cadastrou rotina agora não espera o TTL."""
    global _catalogo
    _catalogo = None
