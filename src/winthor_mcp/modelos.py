"""Os tipos que atravessam o servidor.

Nada aqui conhece Oracle, HTTP ou MCP: são o vocabulário comum entre o
catálogo, a permissão, a identidade e as ferramentas.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, Field


class Modulo(BaseModel):
    """Um módulo do menu do WinThor (PCMODULO)."""

    codigo: int
    nome: str


class Submodulo(BaseModel):
    codigo_modulo: int
    codigo: int
    nome: str


class Execucao(StrEnum):
    """Por onde esta rotina pode ser executada de verdade."""

    WTA = "wta"
    """Tem serviço REST no WinThor Anywhere. Executa nativo."""
    DESKTOP = "desktop"
    """Só existe como .exe Delphi. O servidor não executa e diz isso."""
    DESCONHECIDA = "desconhecida"
    """Ainda não foi resolvida — o WTA não respondeu ou não está configurado."""


class Rotina(BaseModel):
    """Uma linha de PCROTINA, do jeito que uma pessoa entende.

    Tudo vem do banco do próprio cliente. Não há lista fixa de rotinas em
    lugar nenhum deste projeto: um WinThor com rotina específica da casa
    aparece aqui igual às de fábrica.
    """

    codigo: int
    nome: str
    modulo: int | None = None
    modulo_nome: str | None = None
    submodulo: int | None = None
    submodulo_nome: str | None = None
    executavel: str | None = Field(default=None, description="Nome do .exe, ex. PCSIS316")
    versao: str | None = None
    tela_web: bool = False
    """ROTINAWEB='S': a tela roda no navegador, dentro do WTA."""
    no_menu: bool = True
    usos: int | None = None
    ultimo_uso: date | None = None
    execucao: Execucao = Execucao.DESCONHECIDA


class Identidade(BaseModel):
    """Quem é a pessoa, do ponto de vista do ERP."""

    matricula: int
    nome: str | None = None
    codusur: int | None = None
    """Código de vendedor (PCUSUARI), quando a pessoa também é RCA."""
    situacao: str = "A"

    @property
    def ativo(self) -> bool:
        return self.situacao.strip().upper() == "A"


class Erro(BaseModel):
    """Erro estável, para o cliente MCP tratar sem ler texto."""

    codigo: str
    mensagem: str


class Resultado(BaseModel):
    """O retorno de uma execução, com ou sem sucesso."""

    ok: bool
    rotina: int | None = None
    resumo: str | None = None
    dados: dict | list | None = None
    erros: list[Erro] = Field(default_factory=list)
