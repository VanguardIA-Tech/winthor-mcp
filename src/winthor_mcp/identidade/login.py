"""Quem está falando com o servidor.

Medir PCEMPR na base real definiu como conferir senha. Nos 2.590 funcionários
ativos de uma instalação de referência:

- `SENHAHASH` (VARCHAR2 32) está 100% NULL. Não guarda nada.
- `HASHSENHAWINTHOR` (VARCHAR2 250) tem 154 linhas — 6% — em bcrypt (`$2a$`).
- `SENHABD` tem 2.587 preenchidas, na cifra própria do WinThor, cuja fórmula
  foi reconstruída em [[senhabd]] a partir de credenciais reais.

A ordem de autoridade: com **WinThor Anywhere** configurado, é ele quem confere
a senha, como o próprio ERP faz. Sem WTA, o servidor confere localmente por
`SENHABD` (cobre quase todo funcionário ativo) e, quando existe, por bcrypt. Só
recusa por falta de meio — dizendo o que configurar — quando o usuário não tem
nenhum dos dois. O **Oracle é sempre a fonte de identidade e permissão**:
matrícula, nome, codusur, situação.

Nada aqui — nem log, nem exceção, nem mensagem de erro — carrega a senha ou o
hash. O que sai é `Identidade`, `None`, ou uma exceção de configuração.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any, NamedTuple

from winthor_mcp.configuracao import configuracao
from winthor_mcp.identidade import senhabd
from winthor_mcp.modelos import Identidade
from winthor_mcp.oracle import consultar
from winthor_mcp.wta import cliente
from winthor_mcp.wta.cliente import hash_senha

try:  # bcrypt é dependência declarada; ausente, o fallback simplesmente não existe.
    import bcrypt
except ImportError:  # pragma: no cover
    bcrypt = None  # type: ignore[assignment]

COLUNA_BCRYPT = "HASHSENHAWINTHOR"

_identidade: Identidade | None = None
_trava = asyncio.Lock()


class SemComoValidar(RuntimeError):
    """Esta base não permite conferir senha localmente e não há WTA.

    É erro de instalação, não credencial errada — por isso é exceção e não
    `None`: quem chama precisa mostrar o que fazer, não repetir "senha
    inválida" para alguém que digitou certo.
    """


MENSAGEM_SEM_COMO_VALIDAR = (
    "Não há como validar a senha deste usuário nesta instalação: falta "
    "SENHABD/USUARIOBD e senha em bcrypt (HASHSENHAWINTHOR), e o WinThor "
    "Anywhere não está configurado. Defina WINTHOR_WTA_URL apontando para o "
    "WinThor Anywhere do cliente."
)

# As colunas de PCEMPR que este módulo sabe usar para conferir senha. SENHABD
# (cifra própria) cobre quase toda a base; HASHSENHAWINTHOR (bcrypt) é recente
# e existe em poucos. USUARIOBD é a chave da cifra de SENHABD. Whitelist: só
# nome daqui entra no SELECT.
_COLUNAS_AUTENTICACAO = ("USUARIOBD", "SENHABD", COLUNA_BCRYPT)

_colunas_auth: set[str] | None = None


async def _colunas_de_autenticacao(schema: str) -> set[str]:
    """Quais das colunas de senha esta instalação realmente tem.

    Descoberto uma vez e guardado: o dicionário de dados não muda no tempo de
    vida do processo, e perguntar por ORA-00904 a cada login seria caro e feio.
    """
    global _colunas_auth
    if _colunas_auth is not None:
        return _colunas_auth
    linhas = await consultar(
        """
        select column_name
          from all_tab_columns
         where owner = :owner
           and table_name = 'PCEMPR'
           and column_name in ('USUARIOBD', 'SENHABD', 'HASHSENHAWINTHOR')
        """,
        limite=len(_COLUNAS_AUTENTICACAO),
        owner=schema,
    )
    _colunas_auth = {str(linha["column_name"]).upper() for linha in linhas}
    return _colunas_auth


async def _linha_do_funcionario(chave: str, schema: str) -> dict[str, Any] | None:
    """A linha de PCEMPR de quem está tentando entrar, ou `None`.

    Matrícula é chave: casa exato. Nome é prefixo, porque ninguém digita o
    nome completo do jeito que o RH cadastrou — mas nome que casa em mais de
    uma linha é recusa, não escolha. Homônimo e recontratação existem, e
    decidir no palpite quem é a pessoa é o oposto de autenticar.
    """
    disponiveis = await _colunas_de_autenticacao(schema)
    extras = "".join(f", {coluna}" for coluna in _COLUNAS_AUTENTICACAO if coluna in disponiveis)
    # Identificador não aceita bind; o schema já vem em maiúsculas do
    # validador da configuração e as colunas saem da whitelist acima.
    if chave.isdigit():
        linhas = await consultar(
            f"""
            select matricula, nome, situacao, codusur{extras}
              from {schema}.PCEMPR
             where matricula = :matricula
            """,
            limite=2,
            matricula=int(chave),
        )
    else:
        linhas = await consultar(
            f"""
            select matricula, nome, situacao, codusur{extras}
              from {schema}.PCEMPR
             where upper(trim(nome)) like :padrao escape '\\'
            """,
            limite=2,
            padrao=_padrao(chave),
        )
    return linhas[0] if len(linhas) == 1 else None


def _padrao(nome: str) -> str:
    """Prefixo do nome, com os curingas do LIKE neutralizados — senão um
    usuário chamado `%` casaria com a folha inteira."""
    escapado = nome.strip().upper().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escapado}%"


def _bcrypt_confere(guardado: object, senha: str) -> bool:
    """Só aceita o que é reconhecidamente bcrypt (`$2...`). Coluna vazia ou em
    outro formato nunca confere: tratar desconhecido como igual seria login
    sem senha."""
    if bcrypt is None or guardado is None:
        return False
    texto = str(guardado).strip()
    if not texto.startswith("$2"):
        return False
    return any(_checkpw(candidato, texto) for candidato in _candidatos(senha))


def _candidatos(senha: str) -> tuple[str, str]:
    """O que o WinThor passou pelo bcrypt não está documentado, e não dá para
    descobrir sem a base. Duas hipóteses vivas: a senha em claro, ou o
    `MD5(UPPER(senha))` que o cliente manda no login do WTA — se a coluna foi
    escrita por esse caminho, é o MD5 que está lá dentro. Tentar as duas custa
    um `checkpw` a mais só quando a primeira falha.
    """
    md5 = hashlib.md5(senha.upper().encode("utf-8"), usedforsecurity=False).hexdigest().upper()
    return senha, md5


def _checkpw(senha: str, hash_guardado: str) -> bool:
    try:
        return bcrypt.checkpw(senha.encode("utf-8"), hash_guardado.encode("utf-8"))
    except ValueError:
        # Hash truncado pelo VARCHAR2, corrompido na migração, ou senha acima
        # dos 72 bytes que o bcrypt aceita.
        return False


class Autenticado(NamedTuple):
    """O resultado de um login bem-sucedido, com o que a sessão precisa guardar
    para agir como a pessoa no WTA depois."""

    identidade: Identidade
    # PCEMPR.USUARIOBD — o login que o WTA reconhece. `None` se a base não o tem.
    usuario_wta: str | None
    # MD5(maiúsculas(senha)) — o que o WTA recebe no login, guardado para relogar.
    credencial_wta: str | None


async def autenticar(usuario: str, senha: str) -> Identidade | None:
    """Confere a credencial e devolve quem é a pessoa no ERP.

    Atalho de `autenticar_para_sessao` para quem só quer saber quem é a pessoa,
    sem o material de relogin do WTA.
    """
    resultado = await autenticar_para_sessao(usuario, senha)
    return resultado.identidade if resultado is not None else None


async def autenticar_para_sessao(usuario: str, senha: str) -> Autenticado | None:
    """Confere a credencial e devolve identidade + material de sessão do WTA.

    `usuario` é matrícula (o caso normal) ou nome. Recusa é sempre `None`:
    senha errada, pessoa desligada, nome ambíguo e matrícula inexistente são a
    mesma resposta, porque distingui-las aqui entrega metade de um ataque de
    enumeração de graça.

    Levanta `SemComoValidar` quando a instalação não oferece nenhuma forma de
    conferir — isso não é recusa, é configuração faltando.
    """
    chave = usuario.strip()
    if not chave or not senha:
        return None

    conf = configuracao()
    linha = await _linha_do_funcionario(chave, conf.db_schema)
    if linha is None:
        return None
    if str(linha.get("situacao") or "").strip().upper() != "A":
        return None

    matricula = int(linha["matricula"])
    usuario_wta = str(linha["usuariobd"]).strip() if linha.get("usuariobd") else None

    if conf.tem_wta:
        # Com o WTA no ar ele é a autoridade: confere a senha do jeito que o
        # próprio ERP confere e ainda aplica o estado atual do usuário. O login
        # do WTA é o usuário de banco (USUARIOBD), não a matrícula.
        if usuario_wta is None:
            return None
        token = await cliente.entrar(usuario_wta, senha)
        if token is None:
            return None
        # O token é descartado sem logout de propósito: ele expira sozinho, e
        # derrubá-lo pode derrubar junto uma sessão que a mesma pessoa esteja
        # usando no ERP.
    elif not _confere_offline(linha, senha):
        # Sem WTA: SENHABD e bcrypt são as formas de conferir. Se nenhuma
        # existe para este usuário, é configuração faltando, não senha errada.
        if not _tem_como_validar(linha):
            raise SemComoValidar(MENSAGEM_SEM_COMO_VALIDAR)
        return None

    identidade = Identidade(
        matricula=matricula,
        nome=(str(linha["nome"]).strip() if linha.get("nome") else None),
        codusur=(int(linha["codusur"]) if linha.get("codusur") is not None else None),
        situacao="A",
    )
    # O material de relogin só faz sentido — e só é guardado — quando há WTA.
    credencial = hash_senha(senha) if (conf.tem_wta and usuario_wta) else None
    return Autenticado(identidade=identidade, usuario_wta=usuario_wta, credencial_wta=credencial)


def _confere_offline(linha: dict[str, Any], senha: str) -> bool:
    """Confere a senha sem o WTA: primeiro SENHABD, depois bcrypt.

    SENHABD é o caminho principal porque cobre praticamente todo funcionário
    ativo; o bcrypt de HASHSENHAWINTHOR reforça os poucos que o têm.
    """
    if senhabd.verificar(senha, linha.get("usuariobd"), linha.get("senhabd")):
        return True
    return _bcrypt_confere(linha.get(COLUNA_BCRYPT.lower()), senha)


def _tem_como_validar(linha: dict[str, Any]) -> bool:
    """Há material nesta linha para conferir senha localmente?

    Distingue "senha errada" de "não dá para conferir nesta base". Sem isso, um
    usuário sem SENHABD nem bcrypt receberia "credencial inválida" mesmo tendo
    digitado a senha certa — escondendo a causa real, que é configuração.
    """
    if linha.get("usuariobd") and linha.get("senhabd"):
        return True
    if bcrypt is None:
        return False
    guardado = linha.get(COLUNA_BCRYPT.lower())
    return guardado is not None and str(guardado).strip().startswith("$2")


async def identidade_configurada() -> Identidade | None:
    """A pessoa que instalou o servidor, autenticada uma vez por processo.

    A credencial vem do ambiente e não muda enquanto o processo vive. O cache
    guarda só o sucesso: um `None` de Oracle fora do ar ou WTA reiniciando
    travaria o servidor para o resto da vida dele.
    """
    global _identidade
    conf = configuracao()
    usuario, senha = conf.usuario, conf.senha
    if not (usuario and senha):
        return None
    async with _trava:
        if _identidade is not None:
            return _identidade
    identidade = await autenticar(usuario, senha.get_secret_value())
    if identidade is None:
        return None
    async with _trava:
        _identidade = identidade
    return identidade


def esquecer() -> None:
    """Descarta o que foi memorizado. Usado por teste e por reconfiguração."""
    global _identidade, _colunas_auth
    _identidade = None
    _colunas_auth = None
