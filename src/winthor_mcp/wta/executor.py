"""Executa uma operação do catálogo WTA com o token de uma pessoa.

Monta o caminho (parâmetros de rota entram na URL, o resto vira query string),
valida o que é obrigatório e chama o WinThor Anywhere com o Bearer daquela
sessão. Como o token é da pessoa, o WTA aplica a permissão dela e a auditoria
do ERP registra a matrícula certa — sem este servidor reimplementar nada.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from winthor_mcp.modelos import Erro, Resultado
from winthor_mcp.wta.operacoes import Operacao


def validar(op: Operacao, parametros: dict[str, Any]) -> list[Erro]:
    faltando = [
        p.nome for p in op.parametros if p.obrigatorio and parametros.get(p.nome) in (None, "")
    ]
    if not faltando:
        return []
    return [
        Erro(
            codigo="winthor.operacao.parametro_faltando",
            mensagem=f"A operação {op.nome} exige: {', '.join(faltando)}.",
        )
    ]


def montar(op: Operacao, parametros: dict[str, Any]) -> tuple[str, dict[str, Any], Any]:
    """Devolve (caminho, query, corpo). Parâmetros de rota entram no caminho, os
    demais conhecidos viram query, e o parâmetro especial `dados` vira o corpo
    JSON das operações de escrita. Parâmetro não declarado é ignorado — o
    catálogo é a fronteira do que este servidor deixa chamar."""
    caminho = op.caminho_completo
    query: dict[str, Any] = {}
    corpo: Any = None
    declarados = {p.nome: p for p in op.parametros}
    for nome, valor in parametros.items():
        p = declarados.get(nome)
        if p is None or valor in (None, ""):
            continue
        if nome == "dados":
            corpo = valor
        elif p.no_caminho:
            caminho = caminho.replace(f"{{{nome}}}", quote(str(valor), safe=""))
        else:
            query[nome] = valor
    return caminho, query, corpo


async def executar_operacao(op: Operacao, parametros: dict[str, Any], token: str) -> Resultado:
    erros = validar(op, parametros)
    if erros:
        return Resultado(ok=False, resumo=f"Faltam parâmetros para {op.nome}.", erros=erros)
    caminho, query, corpo = montar(op, parametros)
    from winthor_mcp.wta.cliente import chamar

    resposta = await chamar(op.metodo, caminho, token=token, params=query or None, json=corpo)
    return Resultado(
        ok=resposta.ok,
        resumo=(
            f"Operação {op.nome} executada no WinThor Anywhere."
            if resposta.ok
            else f"O WinThor Anywhere recusou a operação {op.nome}."
        ),
        dados=resposta.dados,
        erros=resposta.erros,
    )
