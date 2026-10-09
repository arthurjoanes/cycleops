# One image for migrate, worker and api; Compose picks the command.
# Paths mirror the repository (/app = repo root), so code that resolves alembic.ini,
# policy/ or fixtures/ from the source tree or from the working directory finds them.
FROM ghcr.io/astral-sh/uv:0.13.0@sha256:cdc6093146eb3ff6a40107b38f008b789e050e77ad87865e381d9917da55a168 AS uv

FROM python:3.14.8-slim-trixie@sha256:f85c5697265c178cc6887276c55fe16cf3d14ca35c3df6a5eab3b360534a55d2 AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app/apps/backend
# Dependencies first, in their own layer: they change less often than the code.
COPY apps/backend/pyproject.toml apps/backend/uv.lock apps/backend/.python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY apps/backend/ ./
RUN uv sync --frozen --no-dev

FROM python:3.14.8-slim-trixie@sha256:f85c5697265c178cc6887276c55fe16cf3d14ca35c3df6a5eab3b360534a55d2
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data/archive \
    && chown app:app /data/archive
# Code stays owned by root: the app user reads it and writes only to /data/archive and /tmp.
COPY --from=build /app /app
COPY policy/ /app/policy/
COPY fixtures/synthetic/ /app/fixtures/synthetic/
WORKDIR /app
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    CYCLEOPS_POLICY_DIR=/app/policy
USER app
