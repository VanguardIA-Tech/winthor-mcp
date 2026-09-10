"""O catálogo de operações REST do WinThor Anywhere.

O WTA não expõe "executar rotina NNN": expõe recursos de domínio (clientes,
produtos, pedidos, preços, estoque), cada um num endpoint REST. Este catálogo
lista essas operações a partir da API oficial da TOTVS — que é a mesma em todo
cliente, então isto não é query copiada de um banco específico e não diverge de
instalação para instalação.

Cada operação declara o serviço (o prefixo, que também diz se está instalado),
o método, o caminho e os parâmetros. Só operações de leitura entram por
enquanto; escrever um pedido é possível pela mesma via, mas exige um passo de
confirmação e fica para quando for ligado conscientemente.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Efeito(StrEnum):
    LEITURA = "leitura"
    ESCRITA = "escrita"


@dataclass(frozen=True, slots=True)
class Parametro:
    nome: str
    obrigatorio: bool
    descricao: str
    # Vai na query string (o padrão) ou no caminho (ex. .../available/{filial}/{produto}).
    no_caminho: bool = False


@dataclass(frozen=True, slots=True)
class Operacao:
    nome: str
    servico: str  # prefixo REST, ex. "/api/wholesale/v1"
    metodo: str
    caminho: str  # relativo ao serviço, ex. "/customer/list"
    descricao: str
    efeito: Efeito = Efeito.LEITURA
    parametros: tuple[Parametro, ...] = field(default_factory=tuple)

    @property
    def caminho_completo(self) -> str:
        return f"{self.servico}{self.caminho}"


# Paths conforme a coleção Postman oficial da TOTVS (APIs Integrações WinThor).
# Só leitura; a paginação da maioria é page/pageSize.
_PAGINA = (
    Parametro("page", False, "Página (começa em 1)."),
    Parametro("pageSize", False, "Itens por página."),
)

CATALOGO: tuple[Operacao, ...] = (
    Operacao(
        "listar_clientes",
        "/api/wholesale/v1",
        "GET",
        "/customer/list",
        "Lista os clientes cadastrados.",
        parametros=_PAGINA,
    ),
    Operacao(
        "buscar_cliente",
        "/api/wholesale/v1",
        "GET",
        "/customer/",
        "Busca um cliente pelo código.",
        parametros=(Parametro("customerId", True, "Código do cliente."),),
    ),
    Operacao(
        "listar_pedidos",
        "/api/wholesale/v1",
        "GET",
        "/orders/list",
        "Lista pedidos de venda, por filial e/ou cliente.",
        parametros=(
            Parametro("branchId", False, "Código da filial."),
            Parametro("customerId", False, "Código do cliente."),
            *_PAGINA,
        ),
    ),
    Operacao(
        "consultar_pedido",
        "/api/wholesale/v1",
        "GET",
        "/orders/",
        "Consulta um pedido de venda pelo número.",
        parametros=(Parametro("id", True, "Número do pedido."),),
    ),
    Operacao(
        "listar_precos",
        "/api/wholesale/v1",
        "GET",
        "/price/list",
        "Lista a tabela de preços de uma filial.",
        parametros=(Parametro("branchId", True, "Código da filial."), *_PAGINA),
    ),
    Operacao(
        "listar_produtos",
        "/api/purchases/v1",
        "GET",
        "/products/",
        "Lista os produtos cadastrados.",
        parametros=_PAGINA,
    ),
    Operacao(
        "buscar_produto",
        "/api/purchases/v1",
        "GET",
        "/products/{codigo}",
        "Busca um produto pelo código.",
        parametros=(Parametro("codigo", True, "Código do produto.", no_caminho=True),),
    ),
    Operacao(
        "listar_skus",
        "/api/purchases/v1",
        "GET",
        "/skus/",
        "Lista os SKUs (itens de estoque) dos produtos.",
        parametros=_PAGINA,
    ),
    Operacao(
        "listar_marcas",
        "/api/purchases/v1",
        "GET",
        "/productBrands/",
        "Lista as marcas de produto.",
        parametros=(
            Parametro("companyId", False, "Código da empresa."),
            Parametro("branchId", False, "Código da filial."),
        ),
    ),
    Operacao(
        "listar_departamentos",
        "/api/purchases/v1",
        "GET",
        "/productDepartments",
        "Lista os departamentos de produto.",
    ),
    Operacao(
        "listar_secoes",
        "/api/purchases/v1",
        "GET",
        "/productSections",
        "Lista as seções de produto.",
    ),
    Operacao(
        "listar_categorias",
        "/api/purchases/v1",
        "GET",
        "/productCategories/",
        "Lista as categorias de produto.",
    ),
    Operacao(
        "listar_estoque",
        "/api/stock-vtex/v1",
        "GET",
        "/available/list",
        "Lista o estoque disponível por filial.",
        parametros=(Parametro("branchId", True, "Códigos de filial, separados por vírgula."),),
    ),
    Operacao(
        "consultar_estoque",
        "/api/stock-vtex/v1",
        "GET",
        "/available/{filial}/{produto}",
        "Consulta o estoque disponível de um produto numa filial.",
        parametros=(
            Parametro("filial", True, "Código da filial.", no_caminho=True),
            Parametro("produto", True, "Código do produto.", no_caminho=True),
        ),
    ),
)


def operacao(nome: str) -> Operacao | None:
    return _POR_NOME_TODAS.get(nome)


def por_servico(prefixos_ativos: set[str]) -> list[Operacao]:
    """As operações cujos serviços estão instalados neste WTA."""
    return [op for op in CATALOGO if op.servico in prefixos_ativos]


# --- Operações de escrita ---------------------------------------------------
# Gravam no ERP. Passam pelo mesmo executor, mas o servidor só as deixa rodar
# com confirmar=true e a escrita ligada pelo administrador. O corpo (JSON) vai
# no parâmetro "dados".

CATALOGO_ESCRITA: tuple[Operacao, ...] = (
    Operacao(
        "cadastrar_cliente",
        "/api/wholesale/v1",
        "POST",
        "/customer/",
        "Cadastra um novo cliente. Os campos vão no parâmetro 'dados'.",
        efeito=Efeito.ESCRITA,
        parametros=(Parametro("dados", True, "Objeto com os dados do cliente."),),
    ),
    Operacao(
        "atualizar_cliente",
        "/api/wholesale/v1",
        "PUT",
        "/customer/",
        "Atualiza um cliente existente. Os campos vão no parâmetro 'dados'.",
        efeito=Efeito.ESCRITA,
        parametros=(Parametro("dados", True, "Objeto com os dados do cliente."),),
    ),
    Operacao(
        "criar_pedido",
        "/api/wholesale/v1",
        "POST",
        "/orders/",
        "Grava um pedido de venda. Os itens e o cabeçalho vão em 'dados'.",
        efeito=Efeito.ESCRITA,
        parametros=(Parametro("dados", True, "Objeto com o pedido de venda."),),
    ),
    Operacao(
        "cancelar_pedido",
        "/api/wholesale/v1",
        "DELETE",
        "/orders/",
        "Cancela um pedido de venda.",
        efeito=Efeito.ESCRITA,
        parametros=(
            Parametro("id", True, "Número do pedido."),
            Parametro("reasonCode", False, "Código do motivo do cancelamento."),
            Parametro("sendMessageRca", False, "Avisar o RCA (true/false)."),
        ),
    ),
)

_TODAS = CATALOGO + CATALOGO_ESCRITA
_POR_NOME_TODAS = {op.nome: op for op in _TODAS}


# Liga o código da rotina, como a pessoa o conhece, às operações REST que o WTA
# oferece para ela. É por aqui que "quero a rotina 316" vira uma chamada nativa.
# Rotina fora deste mapa é rotina sem serviço no WTA — o servidor diz isso e não
# inventa SQL. As operações de produto/estoque não vêm de uma rotina específica
# do catálogo, então ficam acessíveis direto por executar_operacao_winthor.
ROTINA_OPERACOES: dict[int, tuple[str, ...]] = {
    302: ("buscar_cliente", "listar_clientes", "cadastrar_cliente", "atualizar_cliente"),
    316: ("criar_pedido", "consultar_pedido", "listar_pedidos"),
    329: ("cancelar_pedido",),
    335: ("consultar_pedido", "listar_pedidos"),
    336: ("consultar_pedido", "listar_pedidos"),
}


def operacoes_da_rotina(codigo: int) -> list[Operacao]:
    nomes = ROTINA_OPERACOES.get(codigo, ())
    return [_POR_NOME_TODAS[n] for n in nomes if n in _POR_NOME_TODAS]
