# Contribuindo

Obrigado pelo interesse. Este documento é curto de propósito.

## Rodando localmente

O projeto usa [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/VanguardIA-Tech/winthor-mcp
cd winthor-mcp
uv sync
```

Copie o exemplo de configuração e preencha com os dados do seu ambiente de
testes:

```bash
cp .env.example .env
```

Para rodar o servidor:

```bash
uv run winthor-mcp
```

## Antes de abrir um PR

Rode os quatro comandos. São exatamente os mesmos que a CI executa, em Python
3.12 e 3.13.

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

Testes não podem depender de um Oracle nem do WTA. Se o seu teste precisa de
dados do ERP, use um duplo — a CI não tem acesso à rede de nenhum cliente.

## A regra de ouro

Duas coisas não entram neste projeto, em hipótese nenhuma:

**1. Catálogo de rotina hardcoded.** Nenhuma lista de rotinas, códigos ou nomes
escrita à mão no código-fonte. O catálogo vem de `PCROTINA`, do banco do próprio
cliente. É isso que faz a rotina específica da casa aparecer igual às de
fábrica, e é isso que impede o projeto de envelhecer a cada release da TOTVS.

**2. Regra de negócio emulada em SQL.** O servidor não monta `INSERT` nem
`UPDATE` contra as tabelas do ERP para reproduzir o efeito de uma rotina.
Execução é assunto do WinThor Anywhere, que aplica as regras da TOTVS.
Reimplementar essas regras em SQL cria uma segunda versão do WinThor cujas
divergências só aparecem no fechamento — e a conta é do cliente.

Se uma rotina não tem serviço no WTA, a resposta correta é dizer que ela só
existe no desktop. Isso é uma funcionalidade, não uma limitação a ser
contornada.

## Pull requests

- Um assunto por PR.
- Português do Brasil em nomes de identificadores, docstrings e mensagens
  voltadas a pessoas, seguindo o que já existe no código.
- Nunca inclua credencial, IP, hostname ou nome de cliente real em código,
  teste, issue ou commit.
