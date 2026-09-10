"""As ferramentas que um cliente MCP enxerga.

Nenhuma recebe senha. Quem é a pessoa depende do transporte: no modo remoto
(HTTP + OAuth) vem da sessão que o token de acesso abriu, então cada conexão
fala pelo seu próprio usuário; no modo local (stdio) vem da identidade fixa do
ambiente. Toda ferramenta que fala de rotina passa antes pela permissão da
pessoa, com a mesma regra da 530.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP

from winthor_mcp.acesso.permissoes import AcessoIndisponivel, negado, pode, rotinas_liberadas
from winthor_mcp.catalogo.rotinas import buscar, modulos
from winthor_mcp.catalogo.rotinas import rotina as buscar_rotina
from winthor_mcp.configuracao import configuracao
from winthor_mcp.execucao import instalados
from winthor_mcp.identidade.login import SemComoValidar, identidade_configurada
from winthor_mcp.modelos import Identidade
from winthor_mcp.oracle import testar as testar_oracle
from winthor_mcp.sessao import usuario as sessao


def _auth() -> Any:
    """O servidor de autorização OAuth, só quando o modo remoto está ligado.

    Montado tardiamente: instanciar o provider exige WINTHOR_PUBLIC_URL, que no
    uso local (stdio) nem existe.
    """
    if not configuracao().modo_remoto:
        return None
    from winthor_mcp.auth import WinthorAuthProvider

    return WinthorAuthProvider()


mcp = FastMCP(
    name="WinThor",
    auth=_auth(),
    instructions=(
        "ERP TOTVS WinThor. O catálogo de rotinas vem do banco do próprio "
        "cliente, então os códigos e nomes são os que essa empresa usa. "
        "Comece por whoami para saber quem está falando e quantas rotinas ela "
        "tem liberadas. listar_rotinas só mostra o que essa pessoa já abre no "
        "WinThor — a permissão é a mesma da rotina 530. descrever_rotina antes "
        "de executar_rotina, sempre: é ela que diz se a rotina roda pelo "
        "WinThor Anywhere ou se só existe no desktop. Rotina só-desktop não é "
        "executada nem contornada por SQL; nesse caso diga à pessoa para abrir "
        "a tela no WinThor. Execução com efeito no ERP exige confirmar=true, e "
        "antes disso mostre à pessoa exatamente o que vai rodar."
    ),
)


def _id_sessao() -> str | None:
    """O token da conexão atual, quando ela veio autenticada por OAuth.

    É a chave da `SessaoUsuario` — e, na execução, o que amarra a chamada ao
    WTA ao token daquela pessoa. `None` no stdio, onde não há request HTTP.
    """
    from fastmcp.server.dependencies import get_access_token

    try:
        token = get_access_token()
    except Exception:
        return None
    return token.token if token is not None else None


async def _quem() -> Identidade:
    """A pessoa por trás desta chamada.

    No remoto, sai da sessão aberta pelo login OAuth; sessão vencida vira um
    pedido claro de refazer login. No local, é a identidade do ambiente, e
    `SemComoValidar` vira mensagem em vez de stack trace porque quem lê é quem
    está instalando o servidor.
    """
    id_sessao = _id_sessao()
    if id_sessao is not None:
        atual = sessao.obter(id_sessao)
        if atual is None:
            raise ValueError("Sua sessão do WinThor expirou. Faça login novamente.")
        return atual.identidade

    try:
        identidade = await identidade_configurada()
    except SemComoValidar as erro:
        raise ValueError(str(erro)) from erro
    if identidade is None:
        if configuracao().tem_identidade:
            raise ValueError(
                "O WinThor recusou a credencial de WINTHOR_USUARIO / "
                "WINTHOR_SENHA. Confira a matrícula e a senha, e se essa "
                "pessoa está com situação ativa no PCEMPR."
            )
        raise ValueError(
            "Sem identidade WinThor. Configure WINTHOR_USUARIO e WINTHOR_SENHA "
            "com a matrícula e a senha que essa pessoa usa no WinThor."
        )
    return identidade


@mcp.tool
async def whoami() -> dict[str, Any]:
    """Quem está usando o servidor, e o tamanho do acesso dessa pessoa."""
    identidade = await _quem()
    try:
        liberadas = await rotinas_liberadas(identidade.matricula)
        total = len(liberadas)
    except AcessoIndisponivel as erro:
        return {
            "identidade": identidade.model_dump(mode="json"),
            "rotinas_liberadas": None,
            "aviso": str(erro),
        }
    return {
        "identidade": identidade.model_dump(mode="json"),
        "rotinas_liberadas": total,
        "wta_configurado": configuracao().tem_wta,
    }


@mcp.tool
async def listar_rotinas(
    busca: Annotated[str | None, "Texto, código ou nome do executável."] = None,
    modulo: Annotated[int | None, "Filtra por módulo do WinThor."] = None,
    apenas_executaveis: Annotated[bool, "Só o que roda pelo WinThor Anywhere."] = False,
    limite: Annotated[int, "Quantas devolver."] = 50,
) -> dict[str, Any]:
    """As rotinas que esta pessoa tem liberadas, do catálogo do próprio ERP."""
    from winthor_mcp.wta.operacoes import operacoes_da_rotina

    identidade = await _quem()
    liberadas = await rotinas_liberadas(identidade.matricula)
    encontradas = [r for r in await buscar(busca, modulo) if r.codigo in liberadas]
    if apenas_executaveis:
        encontradas = [r for r in encontradas if operacoes_da_rotina(r.codigo)]
    return {
        "total": len(encontradas),
        "mostrando": min(limite, len(encontradas)),
        "rotinas": [
            {
                "codigo": r.codigo,
                "nome": r.nome,
                "modulo": r.modulo_nome,
                "executavel_no_wta": bool(operacoes_da_rotina(r.codigo)),
            }
            for r in encontradas[:limite]
        ],
    }


@mcp.tool
async def descrever_rotina(
    codigo: Annotated[int, "Código da rotina, como aparece no WinThor."],
) -> dict[str, Any]:
    """Detalha uma rotina e diz, com todas as letras, se dá para executá-la."""
    identidade = await _quem()
    if not await pode(identidade.matricula, codigo):
        return {"ok": False, "erro": (await negado(codigo, identidade.matricula)).model_dump()}
    rotina = await buscar_rotina(codigo)
    if rotina is None:
        return {
            "ok": False,
            "erro": {
                "codigo": "winthor.rotina.inexistente",
                "mensagem": (
                    f"A rotina {codigo} não existe no catálogo deste WinThor. "
                    "Use listar_rotinas para ver os códigos desta empresa."
                ),
            },
        }
    from winthor_mcp.wta.operacoes import operacoes_da_rotina

    operacoes = operacoes_da_rotina(codigo)
    resposta = rotina.model_dump(mode="json")
    if operacoes:
        resposta["operacoes"] = [
            {
                "nome": op.nome,
                "descricao": op.descricao,
                "efeito": op.efeito.value,
                "parametros": [
                    {"nome": p.nome, "obrigatorio": p.obrigatorio, "descricao": p.descricao}
                    for p in op.parametros
                ],
            }
            for op in operacoes
        ]
        resposta["observacao"] = (
            "Esta rotina tem operações no WinThor Anywhere: chame "
            "executar_operacao_winthor com uma delas."
        )
    elif not configuracao().tem_wta:
        resposta["observacao"] = (
            "WINTHOR_WTA_URL não está configurada, então não dá para executar "
            "nada desta rotina por aqui."
        )
    else:
        resposta["observacao"] = (
            f"A rotina {codigo} não tem operação REST no WinThor Anywhere — "
            "costuma ser relatório ou tela que só existe no desktop. Este "
            "servidor não reproduz a rotina por SQL. Abra-a no WinThor."
        )
    return {"ok": True, "rotina": resposta}


@mcp.tool
async def listar_operacoes_winthor() -> dict[str, Any]:
    """As operações do WinThor Anywhere que dá para chamar aqui.

    São as APIs nativas do WTA (clientes, produtos, pedidos, preços, estoque),
    com os parâmetros de cada uma. É por elas que se consulta e opera o ERP no
    nome da própria pessoa.
    """
    await _quem()
    from winthor_mcp.wta.operacoes import CATALOGO

    return {
        "operacoes": [
            {
                "nome": op.nome,
                "descricao": op.descricao,
                "efeito": op.efeito.value,
                "parametros": [
                    {"nome": p.nome, "obrigatorio": p.obrigatorio, "descricao": p.descricao}
                    for p in op.parametros
                ],
            }
            for op in CATALOGO
        ]
    }


@mcp.tool
async def executar_operacao_winthor(
    operacao: Annotated[str, "Nome da operação (ver listar_operacoes_winthor)."],
    parametros: Annotated[dict[str, Any] | None, "Parâmetros da operação."] = None,
    confirmar: Annotated[bool, "Obrigatório quando a operação grava no ERP."] = False,
) -> dict[str, Any]:
    """Chama uma operação do WinThor Anywhere com o usuário desta pessoa.

    Leitura roda direto. Operação que grava no ERP exige confirmar=true e o
    administrador ter ligado a escrita — antes disso, mostre à pessoa o que vai
    acontecer.
    """
    identidade = await _quem()
    from winthor_mcp.wta.operacoes import Efeito
    from winthor_mcp.wta.operacoes import operacao as buscar_operacao

    op = buscar_operacao(operacao)
    if op is None:
        return {
            "ok": False,
            "erro": {
                "codigo": "winthor.operacao.inexistente",
                "mensagem": (
                    f"Não conheço a operação {operacao!r}. Use "
                    "listar_operacoes_winthor para ver as disponíveis."
                ),
            },
        }
    if not configuracao().tem_wta:
        return {
            "ok": False,
            "erro": {
                "codigo": "winthor.wta.nao_configurado",
                "mensagem": "WINTHOR_WTA_URL não está configurada neste servidor.",
            },
        }
    if op.efeito is Efeito.ESCRITA:
        if not confirmar:
            return {
                "ok": False,
                "status": "precisa_confirmar",
                "resumo": (
                    f"A operação {op.nome} grava no ERP como "
                    f"{identidade.nome or identidade.matricula}, com "
                    f"{parametros or 'nenhum parâmetro'}. Mostre isso para a "
                    "pessoa e só chame de novo com confirmar=true depois do sim."
                ),
            }
        if not configuracao().permitir_escrita:
            return {
                "ok": False,
                "erro": {
                    "codigo": "winthor.escrita.desligada",
                    "mensagem": (
                        "Gravar no ERP está desligado neste servidor. O "
                        "administrador liga em WINTHOR_PERMITIR_ESCRITA."
                    ),
                },
            }

    id_sessao = _id_sessao()
    if id_sessao is None:
        return {
            "ok": False,
            "erro": {
                "codigo": "winthor.sessao.sem_token",
                "mensagem": "Execução de operação exige login por OAuth (modo remoto).",
            },
        }
    token = await sessao.token_wta_da_sessao(id_sessao)
    if token is None:
        return {
            "ok": False,
            "erro": {
                "codigo": "winthor.wta.sem_token",
                "mensagem": (
                    "Não foi possível autenticar no WinThor Anywhere como esta "
                    "pessoa. Faça login novamente."
                ),
            },
        }
    from winthor_mcp.wta.executor import executar_operacao

    return (await executar_operacao(op, parametros or {}, token)).model_dump(mode="json")


@mcp.tool
async def diagnostico() -> dict[str, Any]:
    """O que está de pé: Oracle, identidade, WinThor Anywhere e o catálogo."""
    conf = configuracao()
    oracle_ok = await testar_oracle()
    identidade: str | None = None
    if conf.tem_identidade:
        try:
            quem = await identidade_configurada()
            identidade = "ok" if quem else "credencial recusada pelo WinThor"
        except SemComoValidar as erro:
            identidade = str(erro)
        except Exception as erro:  # noqa: BLE001 - diagnóstico nunca derruba
            identidade = f"falhou: {type(erro).__name__}"
    resultado: dict[str, Any] = {
        "oracle": {"alcancavel": oracle_ok, "dsn": conf.dsn, "schema": conf.db_schema},
        "identidade_configurada": conf.tem_identidade,
        "identidade": identidade,
        "wta": {"configurado": conf.tem_wta, "url": conf.wta_url},
        "escrita_liberada": conf.permitir_escrita,
    }
    if not oracle_ok:
        resultado["proximo_passo"] = (
            "O Oracle não respondeu. Confira WINTHOR_DB_* e se este servidor "
            "tem rota até o banco — o WinThor costuma ficar em rede interna."
        )
        return resultado
    resultado["modulos"] = len(await modulos())
    resultado["rotinas_no_catalogo"] = len(await buscar())
    servicos = await instalados()
    resultado["servicos_wta_instalados"] = len(servicos)
    if servicos and not conf.tem_wta:
        resultado["proximo_passo"] = (
            f"O banco mostra {len(servicos)} serviços do WinThor Anywhere "
            "instalados, mas WINTHOR_WTA_URL não está configurada. Sem ela o "
            "servidor fica só em leitura."
        )
    return resultado
