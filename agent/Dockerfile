# syntax=docker/dockerfile:1

# --- build stage: resolve/install the locked dependency set with uv, install
# the local `agent` project, then pre-warm the local embedding model so the
# final image never needs network access for it at runtime.
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
# Two-pass sync: deps only first (cacheable across source-only changes), then
# the project itself once src/ is present -- `uv sync` before src/ exists
# would fail to build the `agent` wheel (pyproject.toml declares
# packages = ["src/agent"]).
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev
COPY skills ./skills
RUN uv run python -c "from agent.memory import embed; embed('warm up')"

# --- final stage: runtime image with the venv + the Kraken CLI on PATH + the
# pre-warmed embedding model cache (no network needed for either at runtime).
FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Verified installer one-liner from https://github.com/krakenfx/kraken-cli's
# README ("Installation" -> "One-liner"). Installs to $CARGO_HOME/bin
# (defaults to /root/.cargo/bin as root), not a uv/pipx location.
RUN curl --proto '=https' --tlsv1.2 -LsSf \
    https://github.com/krakenfx/kraken-cli/releases/latest/download/kraken-cli-installer.sh | sh

COPY --from=build /app /app
COPY --from=build /root/.fastembed_cache /root/.fastembed_cache
ENV PATH="/app/.venv/bin:/root/.cargo/bin:${PATH}"
# Force huggingface_hub to skip its network revision-check and use the
# pre-warmed cache above -- without this it still dials out to check for a
# newer model revision even when the files are already cached (verified by
# an actual `docker run --network none` failing without this).
ENV HF_HUB_OFFLINE=1
WORKDIR /app

CMD ["python", "-m", "agent.main"]
