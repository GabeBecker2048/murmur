# Murmur: the API, the daily scheduler and the job worker in one process
# (`murmur serve`). CPU-only torch (the lock pins the PyTorch CPU index on
# Linux). Runs as uid 1000 with /data as the only writable path.
FROM python:3.11-slim AS lockfiles

# pyproject.toml and uv.lock with Murmur's own version blanked out. A release changes
# only that version, so the dependency layer below (531 MB) stays byte-identical and
# servers that already have it download a few MB instead (0.9.0-0.9.2 each shipped a
# new copy of it).
WORKDIR /src
COPY pyproject.toml uv.lock ./
RUN sed -i '0,/^version = /s/^version = .*/version = "0.0.0"/' pyproject.toml \
 && sed -i '/^name = "murmur"$/{n;s/^version = .*/version = "0.0.0"/}' uv.lock

FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.4 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, from the version-blanked lock files, so neither source changes
# nor a version bump invalidate this layer.
COPY --from=lockfiles /src/pyproject.toml /src/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# ARROW_DEFAULT_MEMORY_POOL: Arrow's default pool (mimalloc) keeps freed memory; a
# year-long bundle peaked at 400 MB RSS with it, 234 MB with the system allocator
# (measured 2026-11-18), and the API container has 512 MB.
ENV PATH="/app/.venv/bin:$PATH" \
    MURMUR_DATA_DIR=/data \
    HF_HOME=/data/hf-cache \
    MPLCONFIGDIR=/tmp/matplotlib \
    ARROW_DEFAULT_MEMORY_POOL=system

RUN useradd --uid 1000 --create-home murmur && mkdir /data && chown murmur:murmur /data
USER murmur
VOLUME /data

EXPOSE 8100
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 CMD ["murmur", "health"]
CMD ["murmur", "serve"]
