# One image for Render: Caddy, the API and the worker in a single service.
#
# Render attaches a disk to one service only, and the API and the worker both read
# the uploaded files, so they share a container here instead of compose's three.
# The stages repeat apps/web/Dockerfile and services/api/Dockerfile with the same
# pins; keep them in step when those change. Build context: the repository root.

FROM node:24.22.2-alpine@sha256:8a32c8cf649a49915a66e81e7a609223c708d0adabf288ee3f4077d765542f85 AS web
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    PNPM_HOME=/pnpm \
    CI=true
RUN corepack enable
WORKDIR /repo
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/
RUN pnpm install --frozen-lockfile
COPY tsconfig.json tsconfig.base.json ./
COPY apps/web apps/web
RUN pnpm --filter @humani-t/web build

FROM golang:1.26.11-alpine@sha256:ecb67f987aa1a57794c91d19ade7ae0209f2f0f5496aaef9e6ae85ddf92d02ab AS caddy
ARG CADDY_VERSION=v2.11.7
ARG DEPENDENCY_FIXES="golang.org/x/net@v0.62.0 github.com/go-jose/go-jose/v4@v4.1.6"
WORKDIR /src
COPY infrastructure/caddy/main.go ./
RUN go mod init humani-t.local/caddy \
    && go get "github.com/caddyserver/caddy/v2@${CADDY_VERSION}" ${DEPENDENCY_FIXES} \
    && go mod tidy \
    && CGO_ENABLED=0 go build -trimpath -o /usr/local/bin/caddy \
        -ldflags="-s -w -X github.com/caddyserver/caddy/v2.CustomVersion=${CADDY_VERSION}" .

FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv

FROM python:3.13.18-slim-trixie@sha256:09a17cad02668ae61ece136b4fd36ac3849e14f670ebedb637ea3e15580525bc AS api
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3.13 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1
WORKDIR /app
COPY services/api/pyproject.toml services/api/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --extra ner
COPY services/api/alembic.ini ./
COPY services/api/migrations ./migrations
COPY services/api/app ./app
RUN python -m compileall -q app migrations

FROM python:3.13.18-slim-trixie@sha256:09a17cad02668ae61ece136b4fd36ac3849e14f670ebedb637ea3e15580525bc AS runtime
# The container starts as root only to hand the disk to uid 10001; start.sh then
# drops to that user for every process. The Shell needs /root/.ssh to exist.
RUN apt-get update \
    && apt-get upgrade --yes --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall --yes --quiet pip \
    && groupadd --system --gid 10001 humani \
    && useradd --system --uid 10001 --gid humani --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin humani \
    && install --directory --owner=10001 --group=10001 --mode=0700 \
        /var/lib/humani-t/documents /var/lib/caddy \
    && install --directory --mode=0700 /root/.ssh
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HUMANI_T_ENVIRONMENT=production \
    XDG_DATA_HOME=/var/lib/caddy \
    XDG_CONFIG_HOME=/var/lib/caddy
WORKDIR /app
COPY --from=api --chown=root:root /app /app
COPY --from=caddy /usr/local/bin/caddy /usr/bin/caddy
COPY --from=web /repo/apps/web/dist /srv
COPY infrastructure/caddy/Caddyfile /tmp/Caddyfile
COPY infrastructure/render/caddyfile.py infrastructure/render/env.sh \
     infrastructure/render/start.sh infrastructure/render/predeploy.sh \
     infrastructure/render/create_roles.py /render/
COPY infrastructure/render/humani-t-cli /usr/local/bin/humani-t-cli
# The Caddyfile is derived from compose's at build time, so headers and CSP stay one
# source of truth; caddy adapt fails the build if the result is not valid.
RUN mkdir /etc/caddy \
    && python /render/caddyfile.py /tmp/Caddyfile /etc/caddy/Caddyfile \
    && rm /tmp/Caddyfile \
    && PORT=10000 caddy adapt --config /etc/caddy/Caddyfile --adapter caddyfile > /dev/null \
    && chmod 0755 /render/start.sh /render/predeploy.sh /usr/local/bin/humani-t-cli
EXPOSE 10000
CMD ["/render/start.sh"]
