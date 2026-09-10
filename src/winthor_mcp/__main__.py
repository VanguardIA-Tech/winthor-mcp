"""Entrada do servidor.

stdio para uso pessoal local; http para o modo remoto multiusuário, onde cada
pessoa faz login por OAuth com a própria credencial WinThor. O http exige
WINTHOR_PUBLIC_URL, que é a âncora do OAuth.
"""

from __future__ import annotations

import argparse

from winthor_mcp.configuracao import configuracao
from winthor_mcp.servidor import mcp


def main() -> None:
    argumentos = argparse.ArgumentParser(
        prog="winthor-mcp",
        description="Servidor MCP para o ERP TOTVS WinThor.",
    )
    argumentos.add_argument(
        "--transporte",
        choices=("stdio", "http"),
        default="stdio",
        help="stdio para uso local; http para servir a rede da empresa com OAuth.",
    )
    argumentos.add_argument("--host", default="0.0.0.0")
    argumentos.add_argument("--porta", type=int, default=8000)
    lidos = argumentos.parse_args()
    if lidos.transporte == "stdio":
        mcp.run()
        return
    if not configuracao().modo_remoto:
        raise SystemExit(
            "O modo http precisa de WINTHOR_PUBLIC_URL — a URL pública por onde "
            "os clientes MCP chegam, usada como âncora do OAuth."
        )
    mcp.run(transport="http", host=lidos.host, port=lidos.porta)


if __name__ == "__main__":
    main()
