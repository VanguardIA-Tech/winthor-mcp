# syntax=docker/dockerfile:1
#
# Imagem do winthor-mcp. Construa a partir da RAIZ do repositório:
#
#   docker build -f docker/Dockerfile -t winthor-mcp .
#
# O driver oracledb roda em modo thin: nada de Instant Client, tnsnames ou
# wallet dentro da imagem.

# --- build -------------------------------------------------------------------
FROM python:3.12-slim AS build

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# hatchling lê o README declarado em pyproject.toml, então ele precisa vir junto.
COPY pyproject.toml README.md ./
COPY src ./src

RUN uv venv /opt/venv \
    && VIRTUAL_ENV=/opt/venv uv pip install --no-cache .

# --- runtime -----------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN useradd --create-home --uid 10001 winthor

COPY --from=build --chown=winthor:winthor /opt/venv /opt/venv

USER winthor
WORKDIR /home/winthor

# Em produção o servidor roda em HTTP (modo remoto multiusuário com OAuth) na
# porta 8000. Para uso local stdio, sobrescreva o comando: `... winthor-mcp`.
EXPOSE 8000
ENTRYPOINT ["winthor-mcp"]
CMD ["--transporte", "http", "--host", "0.0.0.0", "--porta", "8000"]
