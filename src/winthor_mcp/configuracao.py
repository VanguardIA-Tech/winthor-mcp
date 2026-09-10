"""Tudo que o servidor precisa saber para falar com um WinThor.

Um cliente WinThor tem duas superfícies: o Oracle onde o ERP guarda tudo, e o
WinThor Anywhere (WTA), a plataforma OSGi que expõe as rotinas por REST. O
Oracle é obrigatório — é dele que sai o catálogo e a permissão de cada pessoa.
O WTA é opcional: sem ele o servidor continua útil em leitura e diz com todas
as letras que não executa nada.

A identidade é de quem instalou: matrícula e senha do WinThor desktop, as
mesmas que a pessoa digita no ERP. Elas nunca viajam como parâmetro de tool.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Configuracao(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WINTHOR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Oracle do ERP -----------------------------------------------------
    db_host: str
    db_port: int = 1521
    # Oracle é endereçado por service name, não por SID.
    db_service: str
    db_user: str
    db_password: SecretStr
    # O dono das tabelas raramente é o usuário que lê. Quando não informado,
    # assume-se que são o mesmo, que é o caso mais comum em base própria.
    db_schema: str = ""

    # --- WinThor Anywhere --------------------------------------------------
    # Um único host:porta serve os ~120 serviços: a plataforma é OSGi (Karaf +
    # Pax Web) e todos os bundles compartilham a mesma porta HTTP.
    wta_url: str | None = None
    wta_timeout_segundos: float = 30.0
    # O WTA costuma responder por HTTPS com certificado próprio (auto-assinado)
    # atrás do nginx da empresa. Verificar o certificado quebraria a conexão
    # sem ganho real numa rede interna, então isto é desligável.
    wta_verify_tls: bool = True

    # --- Quem está usando (modo local, uma pessoa) -------------------------
    # Usado no transporte stdio: uma identidade fixa para uso pessoal. No modo
    # remoto (HTTP + OAuth), cada conexão traz a sua, e estes ficam vazios.
    usuario: str | None = None
    senha: SecretStr | None = None

    # --- Servidor remoto e OAuth (modo multiusuário) -----------------------
    # A URL pública por onde os clientes MCP chegam. É a âncora do OAuth: entra
    # no issuer, no audience dos tokens e nos metadados de descoberta. Sem ela,
    # o servidor só serve stdio local.
    public_url: str | None = None
    # Segredo para assinar os tokens de sessão. Sem valor, um é sorteado a cada
    # início — bom para um processo só, ruim para vários: todos precisam do
    # mesmo segredo para validar o token um do outro.
    oauth_secret: SecretStr | None = None
    # Quanto tempo a sessão do navegador vale antes de exigir novo login. O
    # token do WTA (4h) é renovado por baixo sem tocar nesta janela.
    sessao_horas: int = 12

    # --- Comportamento -----------------------------------------------------
    # O catálogo tem ~1700 rotinas e quase nunca muda; a permissão muda quando
    # alguém mexe na 530. Cinco minutos evita reler o ERP a cada pergunta sem
    # deixar uma liberação nova esperando muito.
    cache_segundos: int = 300
    # Escrita desligada por padrão: quem liga é o administrador, conscientemente.
    permitir_escrita: bool = False

    @field_validator("db_schema")
    @classmethod
    def _schema_ou_usuario(cls, valor: str, info) -> str:
        return (valor or info.data.get("db_user", "")).upper()

    @field_validator("wta_url", "public_url")
    @classmethod
    def _sem_barra_final(cls, valor: str | None) -> str | None:
        return valor.rstrip("/") if valor else None

    @property
    def dsn(self) -> str:
        """Easy Connect, que é o que o driver thin entende sem tnsnames."""
        return f"{self.db_host}:{self.db_port}/{self.db_service}"

    @property
    def tem_wta(self) -> bool:
        return self.wta_url is not None

    @property
    def tem_identidade(self) -> bool:
        return bool(self.usuario and self.senha)

    @property
    def modo_remoto(self) -> bool:
        """Servir por HTTP com OAuth exige saber a própria URL pública."""
        return self.public_url is not None


@lru_cache
def configuracao() -> Configuracao:
    """Lida uma vez. Credencial ausente falha quando for usada, não no import."""
    return Configuracao()  # type: ignore[call-arg]
