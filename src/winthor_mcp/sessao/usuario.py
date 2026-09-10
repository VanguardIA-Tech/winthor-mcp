"""A sessão de uma pessoa conectada ao servidor remoto.

Cada pessoa que faz login por OAuth vira uma sessão com a identidade dela e,
quando há WinThor Anywhere, o token WTA dela. É isso que faz a execução cair na
conta certa: a rotina roda com o token daquela matrícula, e a auditoria do
WinThor registra quem foi — não um usuário técnico compartilhado.

O token do WTA vive 4 horas e não tem refresh. Para não obrigar a pessoa a
relogar toda tarde, a sessão guarda o material mínimo para refazer o login no
WTA sozinha: o `MD5(maiúsculas(senha))`, que é exatamente o que o endpoint de
login do WTA recebe — nunca a senha em claro. Fica só na memória do processo,
some no logout e na expiração da sessão, e nunca é registrado em log.

O compromisso é consciente: enquanto a sessão vive, o servidor consegue agir
como a pessoa no WTA. É o mesmo compromisso de qualquer proxy de credencial
para um sistema sem refresh token, e o alcance é limitado pela janela da sessão.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from winthor_mcp.modelos import Identidade

# O token do WTA dura 4h; renovamos um pouco antes para nunca chamar o serviço
# com um token que vai vencer no meio da requisição.
_VIDA_TOKEN_WTA_SEGUNDOS = 4 * 60 * 60
_MARGEM_RENOVACAO_SEGUNDOS = 5 * 60


@dataclass
class SessaoUsuario:
    identidade: Identidade
    # O login que o WTA reconhece é o usuário de banco (PCEMPR.USUARIOBD, ex.
    # "FULANO.SILVA"), não a matrícula. Guardado para relogar por conta própria.
    usuario_wta: str | None
    # O que o WTA precisa junto do login: MD5(maiúsculas(senha)). Os dois são
    # ausentes quando o login foi validado offline sem WTA para autenticar.
    credencial_wta: str | None
    expira_em: float
    _token_wta: str | None = field(default=None, repr=False)
    _token_obtido_em: float = field(default=0.0, repr=False)

    @property
    def matricula(self) -> int:
        return self.identidade.matricula

    def expirada(self, agora: float) -> bool:
        return agora >= self.expira_em

    def _token_wta_valido(self, agora: float) -> bool:
        if self._token_wta is None:
            return False
        idade = agora - self._token_obtido_em
        return idade < _VIDA_TOKEN_WTA_SEGUNDOS - _MARGEM_RENOVACAO_SEGUNDOS

    def guardar_token_wta(self, token: str, agora: float) -> None:
        self._token_wta = token
        self._token_obtido_em = agora


_sessoes: dict[str, SessaoUsuario] = {}


def abrir(
    id_sessao: str,
    identidade: Identidade,
    usuario_wta: str | None,
    credencial_wta: str | None,
    vida_segundos: float,
) -> SessaoUsuario:
    """Registra a sessão de quem acabou de logar. `id_sessao` é o identificador
    opaco que o token de acesso carrega (nunca a matrícula em claro)."""
    sessao = SessaoUsuario(
        identidade=identidade,
        usuario_wta=usuario_wta,
        credencial_wta=credencial_wta,
        expira_em=time.time() + vida_segundos,
    )
    _sessoes[id_sessao] = sessao
    return sessao


def obter(id_sessao: str) -> SessaoUsuario | None:
    """A sessão viva de um identificador, ou `None` se não existe ou expirou.
    Uma sessão expirada é descartada na hora em que é procurada."""
    sessao = _sessoes.get(id_sessao)
    if sessao is None:
        return None
    if sessao.expirada(time.time()):
        _sessoes.pop(id_sessao, None)
        return None
    return sessao


async def token_wta_da_sessao(id_sessao: str) -> str | None:
    """O token WTA desta pessoa, renovado por baixo se preciso.

    Devolve `None` quando não há WTA configurado, quando a sessão morreu, ou
    quando não há credencial guardada para relogar — nesse último caso a pessoa
    precisa autenticar de novo, e quem chama trata isso como sessão expirada.
    """
    from winthor_mcp.configuracao import configuracao

    if not configuracao().tem_wta:
        return None
    sessao = obter(id_sessao)
    if sessao is None:
        return None
    agora = time.time()
    if sessao._token_wta_valido(agora):
        return sessao._token_wta
    if sessao.usuario_wta is None or sessao.credencial_wta is None:
        return None
    from winthor_mcp.wta.cliente import entrar_com_md5

    token = await entrar_com_md5(sessao.usuario_wta, sessao.credencial_wta)
    if token is None:
        return None
    sessao.guardar_token_wta(token, agora)
    return token


def encerrar(id_sessao: str) -> None:
    """Esquece a sessão e, com ela, a credencial WTA em memória."""
    _sessoes.pop(id_sessao, None)


def _limpar_tudo() -> None:
    """Usado por teste."""
    _sessoes.clear()
