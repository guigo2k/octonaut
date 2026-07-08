# syntax=docker/dockerfile:1

FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
WORKDIR /app
COPY operator/pyproject.toml operator/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY operator/src ./src
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
COPY --from=build /app /app
ENV PATH="/app/.venv/bin:${PATH}"
WORKDIR /app

CMD ["kopf", "run", "-A", "--standalone", "-m", "krakosaurus_operator.handlers"]
