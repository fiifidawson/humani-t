# syntax=docker/dockerfile:1
#
# The humani-t API image. One image runs three roles; compose.yaml picks one:
#   api      uvicorn app.main:app                 (the default command below)
#   worker   python -m app.worker
#   migrate  alembic upgrade head                 (one-shot, as the schema-owner role)
#
# Build from the repository root:
#   docker build -t humani-t-api services/api
#
# Base images are pinned by digest so a rebuild cannot silently pick up a different
# image under the same tag. Dependabot proposes the new digest when the tag moves.

FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv

FROM python:3.13.18-slim-trixie@sha256:09a17cad02668ae61ece136b4fd36ac3849e14f670ebedb637ea3e15580525bc AS build

COPY --from=uv /uv /usr/local/bin/uv

# Use the image's Python, never a downloaded one: the runtime stage has only the
# image's interpreter, and the virtual environment links to it by path.
ENV UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.13 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1

WORKDIR /app

# Dependencies first, on their own layer: they change far less often than the code,
# so most rebuilds reuse this layer. --locked fails the build if uv.lock is stale
# instead of quietly resolving something different from what was tested.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app

# The root filesystem is read-only at runtime, so bytecode is compiled now rather
# than written on first import.
RUN python -m compileall -q app migrations


FROM python:3.13.18-slim-trixie@sha256:09a17cad02668ae61ece136b4fd36ac3849e14f670ebedb637ea3e15580525bc AS runtime

LABEL org.opencontainers.image.title="humani-t API" \
      org.opencontainers.image.description="Privacy-preserving evidence synthesis for humanitarian decisions." \
      org.opencontainers.image.source="https://github.com/fiifidawson/humani-t" \
      org.opencontainers.image.licenses="Apache-2.0"

# Debian's security fixes land days before the Python image is rebuilt on them, so
# the image takes them itself. The digest pins the base; the fixes are as of the
# build. pip is a package installer, which nothing at runtime needs; removing it
# shrinks what an attacker inside the container could use and scanners must track.
RUN apt-get update     && apt-get upgrade --yes --no-install-recommends     && rm -rf /var/lib/apt/lists/*     && python -m pip uninstall --yes --quiet pip \
    && groupadd --system --gid 10001 humani \
    && useradd --system --uid 10001 --gid humani --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin humani

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HUMANI_T_ENVIRONMENT=production

WORKDIR /app

COPY --from=build --chown=root:root /app /app

# Numeric, so Kubernetes-style "must run as non-root" checks can verify it
# without reading /etc/passwd.
USER 10001:10001

EXPOSE 8000

# --no-proxy-headers: the API decides the client address itself, believing
# X-Forwarded-For only from HUMANI_T_TRUSTED_PROXIES (ClientAddressMiddleware).
# Uvicorn's own handling would rewrite the address before the API sees the peer.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--no-proxy-headers", "--no-server-header", "--timeout-graceful-shutdown", "20"]
