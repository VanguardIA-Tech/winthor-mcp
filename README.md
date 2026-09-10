# winthor-mcp

Servidor MCP para o ERP **TOTVS WinThor**. Ele lê o catálogo de rotinas direto do
Oracle da sua instalação, resolve a permissão da própria pessoa com a mesma regra
que o ERP aplica e executa rotinas nativamente pelo **WinThor Anywhere (WTA)**.
Este é o primeiro MCP de WinThor — não existe hoje nenhum outro, oficial ou de
comunidade.

A ideia é simples: em vez de manter uma lista de rotinas escrita à mão dentro do
projeto, o servidor pergunta ao seu banco. Numa base real isso dá 1.686 rotinas,
e a rotina que a sua casa desenvolveu aparece exatamente como as de fábrica.
A permissão sai do mesmo lugar de sempre — rotina 530 / `PCCONTRO` para as
rotinas desktop e rotina 807 (perfis) para as rotinas web —, então ninguém
enxerga pelo MCP nada que já não enxergasse no WinThor.

## Onde este servidor roda

**Dentro da rede do cliente.** Não é um SaaS e não há serviço hospedado.

O WinThor é on-premise: o Oracle fica na sua infraestrutura e o WTA quase sempre
responde num IP privado. O servidor precisa alcançar os dois ao mesmo tempo, e o
único lugar onde isso é verdade é dentro da sua rede. Ele fala com o cliente MCP
(Claude Desktop, Claude Code, Cursor) por **stdio**, como um processo local —
não abre porta, não escuta conexão de fora.

## O que ele consegue

- Listar o catálogo de rotinas lido de `PCROTINA`, com módulo, submódulo,
  executável, versão e se a tela é web.
- Descrever uma rotina específica e dizer por onde ela pode ser executada.
- Resolver quem é a pessoa conectada e quais rotinas ela pode abrir, com a regra
  do próprio ERP.
- Executar rotinas que têm serviço no WTA, chamando o serviço REST da TOTVS.
- Diagnosticar a instalação: se o Oracle responde, se o WTA está configurado e
  se a identidade informada é válida.

Ferramentas MCP expostas: `whoami`, `listar_rotinas`, `descrever_rotina`,
`executar_rotina`, `diagnostico`.

## O que ele não consegue

Vale ler esta lista antes de instalar.

- **Rotina sem serviço no WTA não executa.** Muita rotina do WinThor só existe
  como executável Delphi. Nesses casos o servidor declara "só no desktop" e para
  por aí — ele não reimplementa a rotina.
- **Sem `WINTHOR_WTA_URL` configurada, o servidor é só leitura.** Ele continua
  útil para catálogo, permissão e diagnóstico, mas diz com todas as letras que
  não executa nada.
- **Ele não emula regra de negócio em SQL.** O servidor nunca monta `INSERT` ou
  `UPDATE` contra as tabelas do ERP para simular o efeito de uma rotina.
  Reproduzir a regra em SQL seria criar uma segunda versão do WinThor, com
  divergências que só aparecem no fechamento.
- **Ele não amplia permissão de ninguém.** Se a pessoa não tem a rotina liberada
  na 530 ou no perfil da 807, o MCP também não tem.

## Requisitos

- Python 3.12 ou superior (só se você for instalar via `uvx`/`pipx`; com Docker,
  não precisa de Python na máquina).
- Uma conta Oracle **de leitura** no schema do WinThor, com acesso a `PCROTINA`,
  `PCCONTRO`, `PCUSUARI`, `PCMODULO` e às demais tabelas de cadastro.
- Opcionalmente, a URL do WinThor Anywhere. Sem ela o servidor sobe em modo
  leitura.

O driver Oracle roda em **modo thin**: não é preciso instalar Instant Client,
`tnsnames.ora` nem wallet.

## Instalação

### uvx (recomendado)

```bash
uvx winthor-mcp
```

Não instala nada permanente: o `uv` baixa, resolve e executa.

### pipx

```bash
pipx install winthor-mcp
winthor-mcp
```

### Docker

A imagem é construída a partir da raiz do repositório:

```bash
docker build -f docker/Dockerfile -t winthor-mcp .
docker run --rm -i --env-file .env winthor-mcp
```

Ou com Compose:

```bash
docker compose -f docker/compose.yaml run --rm winthor-mcp
```

Use `run`, não `up`: é um servidor stdio, ele precisa do stdin ligado ao cliente
MCP.

## Configuração

Todas as variáveis usam o prefixo `WINTHOR_`. Podem vir do ambiente ou de um
arquivo `.env` no diretório de trabalho. Copie o `.env.example` como ponto de
partida.

| Variável | Obrigatória | Padrão | Para que serve |
| --- | --- | --- | --- |
| `WINTHOR_DB_HOST` | sim | — | Host do Oracle do WinThor. |
| `WINTHOR_DB_PORT` | não | `1521` | Porta do listener Oracle. |
| `WINTHOR_DB_SERVICE` | sim | — | Service name do banco. O driver thin usa Easy Connect; não é SID. |
| `WINTHOR_DB_USER` | sim | — | Usuário Oracle de leitura. |
| `WINTHOR_DB_PASSWORD` | sim | — | Senha desse usuário Oracle. |
| `WINTHOR_DB_SCHEMA` | não | igual ao `DB_USER`, em maiúsculas | Dono das tabelas do ERP, quando não for o mesmo usuário que lê. |
| `WINTHOR_WTA_URL` | não | vazio | Base do WinThor Anywhere, `http://host:porta`. Sem ela o servidor fica só em leitura. |
| `WINTHOR_WTA_TIMEOUT_SEGUNDOS` | não | `30.0` | Tempo máximo de espera por uma chamada REST ao WTA. |
| `WINTHOR_USUARIO` | não | vazio | Matrícula do WinThor de quem vai usar o servidor. |
| `WINTHOR_SENHA` | não | vazio | Senha do WinThor dessa mesma pessoa. |
| `WINTHOR_CACHE_SEGUNDOS` | não | `300` | Validade do cache de catálogo e permissão. |
| `WINTHOR_PERMITIR_ESCRITA` | não | `false` | Libera operações de escrita via WTA. Desligado por padrão. |

Exemplo (host e credenciais fictícios):

```bash
WINTHOR_DB_HOST=oracle.exemplo.local
WINTHOR_DB_PORT=1521
WINTHOR_DB_SERVICE=WINT
WINTHOR_DB_USER=consulta_mcp
WINTHOR_DB_PASSWORD=troque-esta-senha
WINTHOR_DB_SCHEMA=WINTHOR
WINTHOR_WTA_URL=http://winthor.exemplo.local:8080
WINTHOR_USUARIO=1234
WINTHOR_SENHA=troque-esta-senha
WINTHOR_PERMITIR_ESCRITA=false
```

`WINTHOR_USUARIO` e `WINTHOR_SENHA` são a matrícula e a senha que a pessoa já
digita no WinThor desktop. Elas identificam quem está usando o servidor e é
delas que sai a permissão aplicada. Sem elas, o servidor sobe, mas não consegue
resolver a identidade — o `diagnostico` avisa.

## Conectando no cliente MCP

O servidor é stdio. A configuração é a mesma nos três clientes; muda só o
arquivo.

- **Claude Desktop** — `claude_desktop_config.json`
  (macOS: `~/Library/Application Support/Claude/`;
  Windows: `%APPDATA%\Claude\`)
- **Claude Code** — `.mcp.json` na raiz do projeto, ou
  `claude mcp add` na linha de comando
- **Cursor** — `.cursor/mcp.json` no projeto, ou `~/.cursor/mcp.json`

```json
{
  "mcpServers": {
    "winthor": {
      "command": "uvx",
      "args": ["winthor-mcp"],
      "env": {
        "WINTHOR_DB_HOST": "oracle.exemplo.local",
        "WINTHOR_DB_PORT": "1521",
        "WINTHOR_DB_SERVICE": "WINT",
        "WINTHOR_DB_USER": "consulta_mcp",
        "WINTHOR_DB_PASSWORD": "troque-esta-senha",
        "WINTHOR_WTA_URL": "http://winthor.exemplo.local:8080",
        "WINTHOR_USUARIO": "1234",
        "WINTHOR_SENHA": "troque-esta-senha"
      }
    }
  }
}
```

Com Docker, troque o comando:

```json
{
  "mcpServers": {
    "winthor": {
      "command": "docker",
      "args": ["run", "--rm", "-i", "--env-file", "/caminho/para/.env", "winthor-mcp"]
    }
  }
}
```

Depois de salvar, reinicie o cliente e chame `diagnostico` para conferir se o
Oracle responde e se o WTA foi encontrado.

## Como descobrir a URL do WTA na sua instalação

O WinThor Anywhere é uma plataforma OSGi (Karaf + Pax Web). Todos os serviços
compartilham a mesma porta HTTP, então basta descobrir `host` e `porta` uma vez.

**1. Teste as portas comuns.** Na prática, a instalação está numa destas:
`80`, `8080`, `8180`, `8181`, `8182`, `9090`.

**2. Confirme pelo portal.** Se o endereço estiver certo, o portal do WTA
responde em:

```
http://host:porta/portal
```

```bash
curl -I http://winthor.exemplo.local:8080/portal
```

**3. Se nenhuma porta responder, leia a configuração.** A porta fica no arquivo
`org.ops4j.pax.web.cfg`, na chave `org.osgi.service.http.port`.

Caminho no Windows:

```
C:\pcsist\produtos\winthor\etc\org.ops4j.pax.web.cfg
```

Caminho no Linux:

```
/opt/pcsist/produtos/winthor/etc/org.ops4j.pax.web.cfg
```

```bash
grep org.osgi.service.http.port /opt/pcsist/produtos/winthor/etc/org.ops4j.pax.web.cfg
```

O valor encontrado é a porta que vai em `WINTHOR_WTA_URL`.

## Segurança

- **A conta Oracle deve ser só de leitura.** O servidor nunca monta `INSERT` ou
  `UPDATE` contra as tabelas do ERP. Crie um usuário dedicado com `SELECT` e
  nada além disso — assim o limite não depende do código, depende do banco.
- **Escrita vem desligada.** `WINTHOR_PERMITIR_ESCRITA` é `false` por padrão.
  Quem liga é o administrador, conscientemente, sabendo que a partir dali
  execuções via WTA podem alterar dados.
- **A permissão é sempre a da pessoa.** O servidor resolve o acesso com a regra
  do próprio ERP (530 / `PCCONTRO` para desktop, 807 para web). Ele não tem
  caminho para contornar isso.
- **Senha nunca é parâmetro de ferramenta.** Nem a do Oracle, nem a do WinThor.
  As credenciais entram por variável de ambiente ou `.env` e ficam no processo;
  nenhuma ferramenta MCP as recebe, e portanto o modelo não as vê e elas não
  aparecem no histórico da conversa.
- **Não versione o `.env`.** Ele já está no `.gitignore`.
- **O servidor fica na rede do cliente.** Não exponha a máquina que o roda à
  internet: ela tem acesso simultâneo ao Oracle do ERP e ao WTA.

## Licença

MIT. Veja [LICENSE](LICENSE).

## Aviso

Este é um projeto independente e **não tem qualquer vínculo com a TOTVS**.
WinThor, WinThor Anywhere e TOTVS são marcas da TOTVS S.A., citadas aqui apenas
para identificar o sistema com o qual este servidor se comunica. O projeto não é
endossado, patrocinado nem suportado pela TOTVS.
