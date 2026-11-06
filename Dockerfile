# Murmur: the API, the daily scheduler and the job worker in one process
# (`murmur serve`). CPU-only torch (the lock pins the PyTorch CPU index on
# Linux). Runs as uid 1000 with /data as the only writable path.
FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.4 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first so source changes do not invalidate this layer.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    MURMUR_DATA_DIR=/data \
    HF_HOME=/data/hf-cache \
    MPLCONFIGDIR=/tmp/matplotlib

RUN useradd --uid 1000 --create-home murmur && mkdir /data && chown murmur:murmur /data
USER murmur
VOLUME /data

EXPOSE 8100
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 CMD ["murmur", "health"]
CMD ["murmur", "serve"]
