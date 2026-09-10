"""A resposta para "esta pessoa pode abrir esta rotina?".

A regra é a do próprio WinThor, não uma inventada aqui: quem manda é a
liberação que o administrador deu na rotina 530 (desktop) e nos perfis da 802
(telas web). O servidor não amplia nem restringe — se a pessoa não enxerga a
rotina no ERP, também não enxerga por aqui.

Duas fontes, porque o WinThor tem duas:

* `PCCONTRO` — uma linha por usuário × rotina, com `ACESSO` em 'S'/'N'. É a
  tabela grande da base (≈395 mil linhas, ≈4.990 usuários): toda consulta sai
  filtrada por matrícula, nunca varrida inteira.
* `PCPERFILUSUARIOS` + `PCPERFIL` + `PCPERFILCONTRO` — o modelo por perfil
  que a 807 usa para as telas web. Aqui não há flag: a linha em
  `PCPERFILCONTRO` *é* a liberação (nesta base, 1.379 linhas para 3 perfis).
  Como o formato varia entre instalações, as colunas são conferidas em
  `ALL_TAB_COLUMNS` antes de montar o SQL, e a fonte é descartada inteira se
  não bater.

Tudo aqui é *fail closed*: Oracle fora do ar responde "não pode". Uma falha de
rede não pode virar liberação de acesso — o pior que acontece é a pessoa ouvir
que não tem permissão quando tinha, e isso ela resolve olhando o motivo.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from winthor_mcp.configuracao import configuracao
from winthor_mcp.modelos import Erro
from winthor_mcp.oracle import consultar

_log = logging.getLogger(__name__)

CODIGO_SEM_PERMISSAO = "winthor.rotina.sem_permissao"

# Rotinas que administram a própria permissão, citadas nas mensagens para a
# pessoa saber onde o administrador precisa mexer.
ROTINA_PERMISSAO_DESKTOP = 530
ROTINA_PERMISSAO_WEB = 807
ROTINA_PERFIS = 802

# Um usuário no teto enxerga as ~1.955 rotinas distintas da base, e o padrão de
# `consultar` (500) truncaria em silêncio — truncar aqui vira negação falsa.
LIMITE_LINHAS = 5_000

# Oracle não faz bind de identificador: o schema entra no texto do SQL. Só
# passa o que é um nome de objeto legítimo, e nada mais.
_SCHEMA_VALIDO = re.compile(r"^[A-Z][A-Z0-9_$#]*$")

_PCCONTRO = "PCCONTRO"
_PCPERFILCONTRO = "PCPERFILCONTRO"
_PCPERFILUSUARIOS = "PCPERFILUSUARIOS"
_PCPERFIL = "PCPERFIL"

# `PCPERFILUSUARIOS` tem exatamente estas duas colunas na base medida.
_COLUNA_PERFIL_DO_USUARIO = "PERFILID"
_COLUNA_USUARIO_DO_PERFIL = "USUARIOID"

# Candidatos por papel. A coluna escolhida sai sempre destas constantes — nunca
# do texto que veio do banco —, então interpolá-la no SQL não abre porta para
# nada. O primeiro nome de cada tupla é o que a base medida usa; os demais
# cobrem instalações que batizaram a mesma ideia de outro jeito.
_CANDIDATAS_PERFIL = ("PERFILID", "IDPERFIL", "CODPERFIL", "CODIGOPERFIL", "PERFIL")
_CANDIDATAS_ROTINA = ("CODROTINA", "CODIGOROTINA", "NUMROTINA", "ROTINA")
_CANDIDATAS_ID_PERFIL = ("ID", "PERFILID", "CODPERFIL", "IDPERFIL", "CODIGO")
# Só nomes cuja semântica é inequívoca: "SITUACAO" existiria aqui, mas exigiria
# adivinhar se o ativo é 'A' ou 'S' — e adivinhar errado libera perfil desligado.
_CANDIDATAS_ATIVO = ("ATIVO", "FLATIVO")
# Nenhuma coluna de acesso existe em `PCPERFILCONTRO` na base medida (a tabela
# tem só PERFILID e CODROTINA). Fica a lista para o caso de uma instalação que
# tenha: onde houver flag, ela manda; onde não houver, a linha é a liberação.
_CANDIDATAS_ACESSO = ("ACESSO", "LIBERADO", "PERMITIDO", "FLACESSO")

_TIPOS_TEXTO = frozenset({"VARCHAR2", "CHAR", "NVARCHAR2", "NCHAR"})
_TIPOS_NUMERO = frozenset({"NUMBER", "FLOAT", "INTEGER", "BINARY_DOUBLE", "BINARY_FLOAT"})


class AcessoIndisponivel(RuntimeError):
    """A base não tem como responder sobre permissão — e isso não é um "não".

    Sem `PCCONTRO` não existe instalação de WinThor: o que há é schema errado
    na configuração ou credencial sem grant. Devolver conjunto vazio faria o
    servidor negar tudo em silêncio e mandar a pessoa cobrar liberação de um
    administrador que não tem nada a liberar. Quem chama decide o que fazer.
    """


@dataclass(frozen=True, slots=True)
class _PlanoPerfil:
    """Como consultar o caminho web nesta base específica."""

    coluna_perfil: str
    """Coluna de `PCPERFILCONTRO` que aponta para o perfil."""
    coluna_rotina: str
    coluna_acesso: str | None
    """Flag de liberação, quando esta instalação tiver uma."""
    acesso_e_texto: bool
    coluna_id_perfil: str | None
    """Chave de `PCPERFIL`; sem ela a tabela de perfis não entra na consulta."""
    coluna_ativo: str | None
    ativo_e_texto: bool


@dataclass(frozen=True, slots=True)
class _Mapa:
    """O que o dicionário de dados diz sobre as tabelas de permissão."""

    tem_pccontro: bool
    plano_perfil: _PlanoPerfil | None
    motivo_perfil: str | None


@dataclass(frozen=True, slots=True)
class _Entrada:
    expira_em: float
    rotinas: frozenset[int]


# O layout das tabelas não muda enquanto o servidor roda; a liberação muda.
# Por isso o mapa vive sozinho, sem TTL, e o cache por matrícula expira.
_mapa: _Mapa | None = None
_cache: dict[int, _Entrada] = {}
_motivos: dict[int, str] = {}


def _schema() -> str:
    conf = configuracao()
    schema = conf.db_schema.strip().upper()
    if not _SCHEMA_VALIDO.match(schema):
        raise ValueError(
            f"WINTHOR_DB_SCHEMA inválido: {conf.db_schema!r}. "
            "Esperado um nome de schema Oracle (letra seguida de letras, dígitos, _, $ ou #)."
        )
    return schema


def _registrar(matricula: int, motivo: str) -> None:
    """Guarda por que a resposta pode estar mais restrita que a verdade.

    O diagnóstico lê isto para distinguir "não tem permissão" de "não deu para
    perguntar" — da tool as duas coisas saem como negativa.
    """
    anterior = _motivos.get(matricula)
    _motivos[matricula] = f"{anterior} {motivo}" if anterior and anterior != motivo else motivo
    _log.warning("permissão da matrícula %s ficou incompleta: %s", matricula, motivo)


def motivo(matricula: int) -> str | None:
    """Último motivo pelo qual a leitura de permissão desta matrícula falhou."""
    return _motivos.get(matricula)


def _inteiro(valor: Any) -> int | None:
    """Código de rotina, venha ele como for.

    `NUMBER` sem escala chega ora como int, ora como float, e `portavel`
    transforma `Decimal` em string antes de nós vermos. Um código que não vira
    inteiro é lixo de dado — descartar é mais seguro que deixar explodir a
    checagem de permissão inteira.
    """
    match valor:
        case bool():
            return None
        case int():
            return valor
        case float() | Decimal():
            return int(valor)
        case str() if valor.strip():
            try:
                return int(float(valor.strip()))
            except ValueError:
                return None
        case _:
            return None


async def _obter_mapa() -> _Mapa:
    """Descobre o formato das tabelas de permissão nesta instalação.

    `PCCONTRO` é contratual e tem colunas fixas. O caminho web não: em bases
    diferentes `PCPERFILCONTRO` aparece com nomes diferentes para as mesmas
    três ideias (perfil, rotina, liberação), e em várias instalações ele nem é
    usado. Adivinhar nome de coluna e errar dá ORA no meio de uma pergunta
    inocente; perguntar ao dicionário custa uma consulta por processo.
    """
    global _mapa
    if _mapa is not None:
        return _mapa

    schema = _schema()
    linhas = await consultar(
        """
        select table_name, column_name, data_type
          from all_tab_columns
         where owner = :dono
           and table_name in ('PCCONTRO', 'PCPERFILCONTRO', 'PCPERFILUSUARIOS', 'PCPERFIL')
        """,
        limite=LIMITE_LINHAS,
        dono=schema,
    )

    colunas: dict[str, dict[str, str]] = {}
    for linha in linhas:
        tabela = str(linha.get("table_name") or "").upper()
        coluna = str(linha.get("column_name") or "").upper()
        colunas.setdefault(tabela, {})[coluna] = str(linha.get("data_type") or "").upper()

    plano, motivo_perfil = _planejar_perfil(colunas)
    _mapa = _Mapa(
        tem_pccontro=_PCCONTRO in colunas,
        plano_perfil=plano,
        motivo_perfil=motivo_perfil,
    )
    if motivo_perfil:
        _log.info("permissão por perfil (rotina 807) indisponível: %s", motivo_perfil)
    return _mapa


def _planejar_perfil(colunas: dict[str, dict[str, str]]) -> tuple[_PlanoPerfil | None, str | None]:
    """Monta a consulta do caminho web, ou explica por que não dá.

    Na base medida `PCPERFILCONTRO` tem duas colunas e mais nada — `PERFILID` e
    `CODROTINA` —, então a presença da linha é a liberação: aqui, ao contrário
    da `PCCONTRO`, não existe linha de negativa para conferir. Isso vale
    enquanto o formato for esse; qualquer variação em que os papéis de perfil e
    de rotina não sejam reconhecíveis derruba a fonte inteira em vez de virar
    palpite, porque um palpite errado mostraria rotina que a pessoa não enxerga
    no ERP — o oposto do que este módulo existe para fazer.
    """
    contro = colunas.get(_PCPERFILCONTRO)
    if not contro:
        return None, f"{_PCPERFILCONTRO} não existe neste schema."
    usuarios = colunas.get(_PCPERFILUSUARIOS)
    if not usuarios:
        return None, f"{_PCPERFILUSUARIOS} não existe neste schema."
    if _COLUNA_PERFIL_DO_USUARIO not in usuarios or _COLUNA_USUARIO_DO_PERFIL not in usuarios:
        return None, (
            f"{_PCPERFILUSUARIOS} não tem as colunas "
            f"{_COLUNA_PERFIL_DO_USUARIO}/{_COLUNA_USUARIO_DO_PERFIL}."
        )

    perfil = _primeira(contro, _CANDIDATAS_PERFIL)
    rotina = _primeira(contro, _CANDIDATAS_ROTINA)
    faltando = [nome for nome, achada in (("perfil", perfil), ("rotina", rotina)) if achada is None]
    if perfil is None or rotina is None:
        return None, (
            f"{_PCPERFILCONTRO} não tem coluna reconhecível para: {', '.join(faltando)}. "
            "Só as rotinas liberadas na 530 serão consideradas."
        )

    acesso = _primeira(contro, _CANDIDATAS_ACESSO)
    acesso_e_texto = True
    if acesso is not None:
        tipo = contro[acesso]
        if tipo in _TIPOS_TEXTO:
            acesso_e_texto = True
        elif tipo in _TIPOS_NUMERO:
            acesso_e_texto = False
        else:
            # Coluna com nome de flag e tipo que não é flag: em vez de arriscar
            # um predicado sem sentido, esta fonte não responde.
            return None, f"{_PCPERFILCONTRO}.{acesso} é {tipo}, que não é uma flag de liberação."

    # `PCPERFIL` entra só para não deixar perfil desligado liberando rotina
    # (ATIVO='S' nos três perfis desta base). Instalação sem nenhuma coluna de
    # situação não tem o que filtrar — aí a filiação de PCPERFILUSUARIOS basta,
    # porque não existe perfil desligado para ignorar.
    perfis = colunas.get(_PCPERFIL) or {}
    ativo = _primeira(perfis, _CANDIDATAS_ATIVO)
    id_perfil = _primeira(perfis, _CANDIDATAS_ID_PERFIL)
    ativo_e_texto = True
    if ativo is not None:
        # A partir daqui a base *tem* o conceito de perfil desligado. Não dá
        # para seguir ignorando o conceito: seria liberar rotina por um perfil
        # que o administrador desativou. Sem como aplicá-lo, a fonte cai.
        if id_perfil is None:
            return None, (
                f"{_PCPERFIL}.{ativo} existe mas {_PCPERFIL} não tem chave reconhecível "
                "para juntar com o perfil do usuário."
            )
        tipo_ativo = perfis[ativo]
        if tipo_ativo in _TIPOS_NUMERO:
            ativo_e_texto = False
        elif tipo_ativo not in _TIPOS_TEXTO:
            return None, f"{_PCPERFIL}.{ativo} é {tipo_ativo}, que não é uma flag de situação."

    return (
        _PlanoPerfil(
            coluna_perfil=perfil,
            coluna_rotina=rotina,
            coluna_acesso=acesso,
            acesso_e_texto=acesso_e_texto,
            coluna_id_perfil=id_perfil if ativo else None,
            coluna_ativo=ativo,
            ativo_e_texto=ativo_e_texto,
        ),
        None,
    )


def _primeira(colunas: dict[str, str], candidatas: tuple[str, ...]) -> str | None:
    return next((nome for nome in candidatas if nome in colunas), None)


async def _rotinas_desktop(matricula: int, schema: str) -> set[int]:
    """As liberações da 530, que é o que responde por quase todo o ERP.

    O `UPPER(TRIM(NVL(...)))` não é preciosismo: a coluna é `VARCHAR2` e bases
    antigas guardam 's', ' S' e nulo onde deveria haver 'N'.
    """
    linhas = await consultar(
        f"""
        select distinct codrotina
          from {schema}.{_PCCONTRO}
         where codusuario = :matricula
           and upper(trim(nvl(acesso, 'N'))) = 'S'
        """,
        limite=LIMITE_LINHAS,
        matricula=matricula,
    )
    return {codigo for linha in linhas if (codigo := _inteiro(linha.get("codrotina"))) is not None}


async def _rotinas_perfil(matricula: int, schema: str, plano: _PlanoPerfil) -> set[int]:
    """As liberações por perfil, de onde saem as telas web da 807.

    O `DISTINCT` não é enfeite: dois perfis da mesma pessoa costumam liberar a
    mesma rotina, e quem chama quer códigos, não repetições.
    """
    juncoes = [
        f"join {schema}.{_PCPERFILCONTRO} pc "
        f"on pc.{plano.coluna_perfil} = pu.{_COLUNA_PERFIL_DO_USUARIO}"
    ]
    if plano.coluna_id_perfil and plano.coluna_ativo:
        ativo = (
            f"upper(trim(nvl(p.{plano.coluna_ativo}, 'N'))) = 'S'"
            if plano.ativo_e_texto
            else f"nvl(p.{plano.coluna_ativo}, 0) = 1"
        )
        juncoes.append(
            f"join {schema}.{_PCPERFIL} p "
            f"on p.{plano.coluna_id_perfil} = pu.{_COLUNA_PERFIL_DO_USUARIO} and {ativo}"
        )
    filtro_acesso = ""
    if plano.coluna_acesso:
        filtro_acesso = (
            f"and upper(trim(nvl(pc.{plano.coluna_acesso}, 'N'))) = 'S'"
            if plano.acesso_e_texto
            else f"and nvl(pc.{plano.coluna_acesso}, 0) = 1"
        )
    linhas = await consultar(
        f"""
        select distinct pc.{plano.coluna_rotina} as codrotina
          from {schema}.{_PCPERFILUSUARIOS} pu
          {" ".join(juncoes)}
         where pu.{_COLUNA_USUARIO_DO_PERFIL} = :matricula
           {filtro_acesso}
        """,
        limite=LIMITE_LINHAS,
        matricula=matricula,
    )
    return {codigo for linha in linhas if (codigo := _inteiro(linha.get("codrotina"))) is not None}


async def rotinas_liberadas(matricula: int) -> set[int]:
    """A união das duas fontes: o que a 530 liberou mais o que os perfis liberam.

    Levanta `AcessoIndisponivel` quando a base não tem `PCCONTRO`. Qualquer
    outra falha devolve o que deu para apurar — no limite, conjunto vazio — e
    deixa o motivo registrado. Resposta a menos é incômodo; resposta a mais é
    vazamento.
    """
    agora = time.monotonic()
    entrada = _cache.get(matricula)
    if entrada is not None and entrada.expira_em > agora:
        return set(entrada.rotinas)

    _motivos.pop(matricula, None)
    schema = _schema()

    try:
        mapa = await _obter_mapa()
    except AcessoIndisponivel:
        raise
    except Exception as erro:  # qualquer falha vira negativa, nunca liberação
        _registrar(matricula, f"o dicionário de dados do schema {schema} não respondeu: {erro}")
        return set()

    if not mapa.tem_pccontro:
        raise AcessoIndisponivel(
            f"{schema}.{_PCCONTRO} não existe ou não é visível para este usuário do banco. "
            "Confira WINTHOR_DB_SCHEMA e o grant de SELECT."
        )

    liberadas: set[int] = set()
    completo = True

    try:
        liberadas |= await _rotinas_desktop(matricula, schema)
    except Exception as erro:
        completo = False
        _registrar(matricula, f"{_PCCONTRO} não pôde ser lida: {erro}")

    if mapa.plano_perfil is not None:
        try:
            liberadas |= await _rotinas_perfil(matricula, schema, mapa.plano_perfil)
        except Exception as erro:
            completo = False
            _registrar(matricula, f"{_PCPERFILCONTRO} não pôde ser lida: {erro}")
    elif mapa.motivo_perfil:
        # Formato incompatível é estável: registrar sim, invalidar o cache não.
        _registrar(matricula, mapa.motivo_perfil)

    if completo:
        # Falha transitória não entra no cache: uma indisponibilidade de rede
        # não pode continuar negando acesso pelos próximos `cache_segundos`.
        _cache[matricula] = _Entrada(
            expira_em=agora + configuracao().cache_segundos,
            rotinas=frozenset(liberadas),
        )
    return liberadas


async def pode(matricula: int, rotina: int) -> bool:
    """Esta pessoa abre esta rotina no WinThor dela?"""
    return rotina in await rotinas_liberadas(matricula)


async def negado(rotina: int, matricula: int) -> Erro:
    """A recusa, escrita para a pessoa saber o que fazer em seguida.

    Diz onde a permissão mora, porque "acesso negado" sem endereço só gera um
    chamado a mais para o time de TI.
    """
    partes = [
        f"Você não tem permissão para a rotina {rotina}.",
        f"A liberação aqui é exatamente a mesma da rotina {ROTINA_PERMISSAO_DESKTOP} do "
        f"WinThor (e, no caso das telas web, da rotina {ROTINA_PERMISSAO_WEB}, que aplica "
        f"os perfis da {ROTINA_PERFIS}): a matrícula {matricula} não está liberada "
        "para essa rotina.",
        "Peça ao administrador do WinThor para liberá-la e tente de novo.",
    ]
    if (registrado := motivo(matricula)) is not None:
        partes.append(
            f"Observação para o suporte: a lista de liberações pode estar incompleta — {registrado}"
        )
    return Erro(codigo=CODIGO_SEM_PERMISSAO, mensagem=" ".join(partes))


def invalidar(matricula: int | None = None) -> None:
    """Esquece o que foi lido. Sem argumento, esquece tudo.

    Chamado depois que alguém mexeu na 530 e não quer esperar o TTL, e pelos
    testes — que precisam do módulo sem memória entre um caso e outro.
    """
    global _mapa
    if matricula is None:
        _cache.clear()
        _motivos.clear()
        _mapa = None
        return
    _cache.pop(matricula, None)
    _motivos.pop(matricula, None)
