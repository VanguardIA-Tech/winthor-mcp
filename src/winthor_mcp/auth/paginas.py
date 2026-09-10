"""As telas HTML do login WinThor no fluxo OAuth.

Mínimo proposital: um formulário de matrícula e senha, sem framework de front,
porque isso abre uma vez por sessão dentro do navegador que o cliente MCP
controla. O texto é para a pessoa da distribuidora, não para quem programa.
"""

from __future__ import annotations

from html import escape

_ESTILO = """
  body { font-family: system-ui, sans-serif; background:#0f172a; color:#e2e8f0;
         display:flex; min-height:100vh; align-items:center; justify-content:center; margin:0 }
  form { background:#1e293b; padding:2rem 2.25rem; border-radius:12px; width:320px;
         box-shadow:0 10px 40px rgba(0,0,0,.4) }
  h1 { font-size:1.15rem; margin:0 0 .35rem }
  p.sub { margin:0 0 1.25rem; color:#94a3b8; font-size:.85rem }
  label { display:block; font-size:.8rem; margin:.75rem 0 .3rem; color:#cbd5e1 }
  input { width:100%; box-sizing:border-box; padding:.6rem .7rem; border-radius:8px;
          border:1px solid #334155; background:#0f172a; color:#e2e8f0; font-size:.95rem }
  button { width:100%; margin-top:1.4rem; padding:.7rem; border:0; border-radius:8px;
           background:#2563eb; color:#fff; font-size:.95rem; font-weight:600; cursor:pointer }
  button:hover { background:#1d4ed8 }
  .erro { background:#7f1d1d; color:#fecaca; padding:.6rem .7rem; border-radius:8px;
          font-size:.82rem; margin-bottom:1rem }
"""


def pagina_login(pedido: str, erro: str | None = None) -> str:
    """O formulário. `pedido` amarra o POST à autorização OAuth em curso."""
    bloco_erro = f'<div class="erro">{escape(erro)}</div>' if erro else ""
    return f"""<!doctype html>
<html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Entrar no WinThor</title><style>{_ESTILO}</style></head>
<body>
  <form method="post" action="/winthor/login">
    <h1>WinThor</h1>
    <p class="sub">Entre com a sua matrícula e senha do WinThor.</p>
    {bloco_erro}
    <input type="hidden" name="pedido" value="{escape(pedido)}">
    <label for="matricula">Matrícula</label>
    <input id="matricula" name="matricula" autocomplete="username" autofocus required>
    <label for="senha">Senha</label>
    <input id="senha" name="senha" type="password" autocomplete="current-password" required>
    <button type="submit">Entrar</button>
  </form>
</body></html>"""
