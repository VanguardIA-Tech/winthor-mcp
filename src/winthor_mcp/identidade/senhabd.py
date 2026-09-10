"""Validar a senha do WinThor pela cifra do ``PCEMPR.SENHABD``.

O WinThor guarda a senha do desktop numa cifra própria e reversível, não num
hash. Foi reconstruída a partir de pares senha/cifra reais e vale para as três
credenciais que serviram de teste (matrículas 22, 263 e 1297 desta base):

    SENHABD = FF FE  +  [ senha[i]  XOR  maiúsculas(USUARIOBD)[i] ]

- ``FF FE`` é o BOM de UTF-16LE que o Delphi grava na frente do campo.
- A chave é o ``USUARIOBD`` (o login de banco, ex. ``FULANO.SILVA``), sempre em
  maiúsculas, um caractere por caractere da senha.
- A senha entra **como digitada**: o WinThor é sensível a maiúsculas. Digitar o
  case errado falha aqui exatamente como falharia na tela do ERP.
- Um byte de resultado ``0x20`` (espaço) é gravado como ``0xFE``. Espaço no meio
  do campo seria comido pelo ``TRIM`` do Oracle; a cifra o escapa para não se
  perder. Acontece quando senha e login coincidem numa posição.

Validar é **cifrar de novo e comparar** — a senha guardada nunca é revertida.
Isso cobre praticamente toda a base: todo funcionário ativo tem ``USUARIOBD`` e
``SENHABD``. É o caminho de autenticação quando não há WinThor Anywhere à mão,
que é a situação comum de um servidor rodando fora da rede onde o WTA vive.
"""

from __future__ import annotations

# O campo é texto no charset do banco (WE8MSWIN1252). Recuperar os bytes
# originais para comparar byte a byte exige o mesmo code page.
_CODEPAGE = "cp1252"
_BOM = b"\xff\xfe"
_ESCAPADO = 0xFE
_ESPACO = 0x20


def cifrar(senha: str, usuariobd: str) -> bytes | None:
    """Reproduz o SENHABD para uma senha e um login. ``None`` se o login for
    curto demais para cobrir a senha — caso raro que este código não arrisca
    adivinhar (ver ``verificar``)."""
    chave = usuariobd.upper()
    if len(chave) < len(senha):
        return None
    corpo = bytearray(_BOM)
    for posicao, caractere in enumerate(senha):
        byte = ord(caractere) ^ ord(chave[posicao])
        corpo.append(_ESCAPADO if byte == _ESPACO else byte)
    return bytes(corpo)


def verificar(senha: str, usuariobd: str | None, senhabd: str | None) -> bool:
    """A senha confere com o SENHABD guardado para este login?

    Fecha em falso sempre que faltar dado ou o formato fugir do conhecido: uma
    dúvida aqui é senha recusada, nunca senha aceita.
    """
    if not senha or not usuariobd or not senhabd:
        return False
    try:
        guardado = senhabd.encode(_CODEPAGE)
    except UnicodeEncodeError:
        return False
    if not guardado.startswith(_BOM):
        return False
    calculado = cifrar(senha, usuariobd)
    if calculado is None:
        return False
    # Comparação direta de bytes; o comprimento diferente já reprova.
    return calculado == guardado
