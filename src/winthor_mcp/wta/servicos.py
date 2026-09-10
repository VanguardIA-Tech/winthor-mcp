"""O que está de fato instalado neste WinThor Anywhere.

Nenhuma instalação tem os ~120 bundles: o cliente sobe os que comprou. Como
tudo divide a mesma porta, descobrir o que existe é perguntar prefixo por
prefixo — cada serviço publica um `/health` que responde sem token.

Sondar sem autenticar é de propósito: o servidor precisa saber o que existe
para dizer "esta rotina não executa por aqui" antes de ter qualquer sessão.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from pydantic import BaseModel

from winthor_mcp.configuracao import configuracao
from winthor_mcp.wta.cliente import cliente, requisitar

# Um serviço fora do ar responde rápido ou não responde; esperar o timeout
# cheio da configuração multiplicaria por treze a espera da primeira pergunta.
TIMEOUT_SONDA_SEGUNDOS = 5.0

PREFIXO_FULFILLMENT = "/winthor/integracao/fulfillment/v1"


class Servico(BaseModel):
    """Um bundle do WTA, do ponto de vista de quem vai chamá-lo."""

    nome: str
    """Nome do bundle OSGi, ex. winthor-pedido-venda."""
    prefixo: str
    """Raiz dos caminhos REST deste serviço, sem barra final."""
    dominio: str
    """O que ele resolve, em português, para casar com a pergunta de alguém."""
    ativo: bool = False


# Prefixos confirmados na documentação TOTVS. A lista é o que o servidor sabe
# perguntar; o que o cliente tem é o `/health` que responder.
CATALOGO: tuple[Servico, ...] = (
    Servico(
        nome="winthor-pedido-venda",
        prefixo="/api/wholesale/v1",
        dominio="pedido de venda, clientes, preços",
    ),
    Servico(
        nome="winthor-compras-produto",
        prefixo="/api/purchases/v1",
        dominio="produtos, SKU, marcas, departamentos",
    ),
    Servico(nome="winthor-filial", prefixo="/api/branch/v1", dominio="filiais"),
    Servico(
        nome="winthor-estoque-vtex",
        prefixo="/api/stock-vtex/v1",
        dominio="estoque disponível",
    ),
    Servico(
        nome="winthor-venda",
        prefixo="/winthor/venda/v0",
        dominio="operadoras, planos de pagamento",
    ),
    Servico(
        nome="winthor-tributacao",
        prefixo="/winthor/tributacao/v0",
        dominio="ICMS, PIS/COFINS, NCM",
    ),
    Servico(
        nome="winthor-integracao-precos",
        prefixo="/winthor/precos/v1",
        dominio="preços fixos, por região",
    ),
    Servico(
        nome="winthor-integracao-cliente",
        prefixo="/winthor/cliente/v1",
        dominio="região, ramo de atividade",
    ),
    Servico(
        nome="winthor-integracao-cadastros",
        prefixo="/winthor/cadastros/v1",
        dominio="profissionais",
    ),
    Servico(
        nome="winthor-fiscal",
        prefixo="/winthor/fiscal/v1",
        dominio="documentos fiscais, NF-e",
    ),
    Servico(
        nome="winthor-logistica-apis",
        prefixo="/winthor/logistic-operator/v1",
        dominio="operador logístico",
    ),
    Servico(
        nome="winthor-ferramenta-usuario",
        prefixo="/winthor/ferramenta/usuario/v1",
        dominio="usuários",
    ),
    Servico(
        nome="winthor-integracao-2650",
        prefixo=PREFIXO_FULFILLMENT,
        dominio="WSH, carga de dados",
    ),
)

_cache: list[Servico] | None = None
_trava = asyncio.Lock()


async def _sondar(servico: Servico) -> Servico:
    """Um `/health` de pé é a resposta. O corpo não é verificado: o texto
    ("Aplicação ativa") já variou entre versões e não vale barrar por ele."""
    http = await cliente()
    try:
        resposta = await http.get(
            f"{servico.prefixo}/health",
            timeout=TIMEOUT_SONDA_SEGUNDOS,
        )
    except httpx.HTTPError:
        return servico.model_copy(update={"ativo": False})
    return servico.model_copy(update={"ativo": resposta.is_success})


async def disponiveis(*, recarregar: bool = False) -> list[Servico]:
    """Os serviços que responderam, sondados em paralelo e memorizados.

    A instalação não muda no meio de uma conversa, e treze requisições por
    pergunta seriam treze requisições desperdiçadas.
    """
    global _cache
    if not configuracao().tem_wta:
        return []
    async with _trava:
        if _cache is not None and not recarregar:
            return list(_cache)
    sondados = await asyncio.gather(*(_sondar(servico) for servico in CATALOGO))
    ativos = [servico for servico in sondados if servico.ativo]
    async with _trava:
        _cache = ativos
    return list(ativos)


async def ativo(prefixo: str) -> bool:
    return any(servico.prefixo == prefixo for servico in await disponiveis())


async def rotas_publicadas(integracao: str = "pdvsync") -> dict[str, Any] | list[Any] | None:
    """As rotas que o próprio WTA monta, quando o fulfillment está instalado.

    Vale mais que a lista de prefixos daqui: as URLs vêm do servidor, já com o
    host e a porta que ele conhece de si mesmo. Ausência é o caso comum — a
    maioria das instalações não tem esse bundle — e não é erro.
    """
    if not await ativo(PREFIXO_FULFILLMENT):
        return None
    resultado = await requisitar(
        "GET",
        f"{PREFIXO_FULFILLMENT}/layout/resolverUrlsRotasWta",
        params={"integracao": integracao},
    )
    return resultado.dados if resultado.ok else None


def esquecer() -> None:
    """Descarta a sondagem. Serve a quem reconfigura o WTA em processo vivo."""
    global _cache
    _cache = None
