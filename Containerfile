# Paperflow workspace container.
#
# Built as localhost/paperflow:local and run rootless under the
# dedicated `paperflow` account (see deploy/paperflow.container). Toolchain
# versions mirror .toolchain.env at the repo root.
#
# The container binds 0.0.0.0:8000 (published by the example Quadlet on loopback) and keeps all mutable state under /data, which the Quadlet maps
# to /srv/paperflow on the host. Relative default paths (artifacts/radar.db,
# artifacts/publishing-corpus.json) resolve against the /data working
# directory.

FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.6 /uv /uvx /usr/local/bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

# Install locked dependencies first so source edits do not invalidate this layer.
COPY pyproject.toml uv.lock README.md ./
COPY paperflow_reader ./paperflow_reader
RUN uv sync --locked --no-dev --no-install-project

COPY *.py ./
RUN uv sync --locked --no-dev

RUN useradd --uid 1000 --user-group --create-home flow

USER 1000:1000
WORKDIR /data
EXPOSE 8000

CMD ["paperflow", "serve", "--host", "0.0.0.0", "--port", "8000", "--config", "/data/arxiv-radar.yaml"]
