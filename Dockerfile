# syntax=docker/dockerfile:1.7
# One image for every service; docker-compose.yml picks the service with `agent-mesh serve`.
# OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false: the SDK traces its internal event queue, about
# 85% of all spans per question; our own server and GenAI spans already cover each hop.

FROM python:3.15-slim@sha256:94d3426f1def7db4a106a232ac121bfd975393e5dfd40a619b955fae45fe7913 AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

COPY --from=ghcr.io/astral-sh/uv:0.13.4@sha256:9f130421e930bfbdc563c00740466603f8723158e90297d07957c2294640262b /uv /bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM python:3.15-slim@sha256:94d3426f1def7db4a106a232ac121bfd975393e5dfd40a619b955fae45fe7913

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    AGENT_MESH_KEYS_DIR=/run/agent-mesh/keys \
    AGENT_MESH_KILL_SWITCH_PATH=/run/agent-mesh/state/kill_switch.json \
    OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false

RUN groupadd --system --gid 1001 mesh \
    && useradd --system --uid 1001 --gid mesh --no-create-home --shell /usr/sbin/nologin mesh \
    && mkdir -p /run/agent-mesh/keys /run/agent-mesh/state \
    && chown -R mesh:mesh /run/agent-mesh

WORKDIR /app
COPY --from=builder --chown=mesh:mesh /app/.venv /app/.venv
COPY --chown=mesh:mesh evals ./evals

USER mesh
ENTRYPOINT ["agent-mesh"]
CMD ["--help"]
