# syntax=docker/dockerfile:1
#
# The humani-t web image: Caddy serving the built single-page app, and forwarding
# /api to the API container. Build from the repository root, because the pnpm
# workspace files live there:
#   docker build -f apps/web/Dockerfile -t humani-t-web .
#
# Base images are pinned by digest so a rebuild cannot silently pick up a different
# image under the same tag. Dependabot proposes the new digest when the tag moves.

FROM node:24.22.2-alpine@sha256:8a32c8cf649a49915a66e81e7a609223c708d0adabf288ee3f4077d765542f85 AS deps

# Corepack installs the exact pnpm named by "packageManager" in package.json, so
# the image and every contributor build with the same pnpm.
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0 \
    PNPM_HOME=/pnpm \
    CI=true
RUN corepack enable

WORKDIR /repo

# Manifests first: the dependency layer is rebuilt only when they change.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/
RUN --mount=type=cache,id=pnpm-store,target=/pnpm/store \
    pnpm install --frozen-lockfile


FROM deps AS build

COPY tsconfig.json tsconfig.base.json ./
COPY apps/web apps/web
RUN pnpm --filter @humani-t/web build


# Caddy is compiled here rather than taken from the official image, whose binary
# can lag behind security releases of Go and of Caddy's dependencies. Caddy faces
# the internet, so those fixes must reach it as soon as they ship. CADDY_VERSION
# must match the runtime image's tag below; Dependabot updates the tags, not this.
FROM golang:1.26.10-alpine@sha256:a2012b4a1d1f9f7e9f44e0d39b805a901e856b1dd80b4e6ee4338db547050308 AS caddy

ARG CADDY_VERSION=v2.11.7
# Dependencies raised above what Caddy's go.mod asks for, each fixing a HIGH
# vulnerability Trivy reports in the release build. Remove an entry once Caddy's
# own go.mod requires that version or newer.
ARG DEPENDENCY_FIXES="golang.org/x/net@v0.60.0 github.com/go-jose/go-jose/v4@v4.1.6"

WORKDIR /src
COPY infrastructure/caddy/main.go ./
# Go checks every module it downloads against the public checksum database, so a
# tampered dependency fails the build.
RUN go mod init humani-t.local/caddy \
    && go get "github.com/caddyserver/caddy/v2@${CADDY_VERSION}" ${DEPENDENCY_FIXES} \
    && go mod tidy \
    && CGO_ENABLED=0 go build -trimpath -o /usr/local/bin/caddy \
        -ldflags="-s -w -X github.com/caddyserver/caddy/v2.CustomVersion=${CADDY_VERSION}" .


FROM caddy:2.11.7-alpine@sha256:e70b164da81ef698b3ad19af799ea6086ce6d9e48ed4079eb9fe26f6fbf14ee5 AS runtime

LABEL org.opencontainers.image.title="humani-t web" \
      org.opencontainers.image.description="Caddy serving the humani-t single-page app." \
      org.opencontainers.image.source="https://github.com/fiifidawson/humani-t" \
      org.opencontainers.image.licenses="Apache-2.0"

# The base image keeps its CA certificates, MIME types and directory layout; only
# the binary is replaced. Ours carries no file capability: the upstream one asks
# for cap_net_bind_service, and with every capability dropped in compose.yaml the
# kernel refuses to run a binary that asks for one. Docker already lets any user
# bind ports 80 and 443 inside a container.
COPY --from=caddy /usr/local/bin/caddy /usr/bin/caddy

# curl, with libcurl and brotli, comes with the base image; nothing here uses it,
# and it is where the base image's known vulnerabilities sit.
# /data holds certificates and the ACME account; /config, Caddy's autosave. Both
# must belong to the unprivileged user before a volume is first created from them.
RUN apk del --no-cache curl \
    && mkdir -p /data/caddy /config/caddy \
    && chown -R 10001:10001 /data /config

COPY infrastructure/caddy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /repo/apps/web/dist /srv

USER 10001:10001

EXPOSE 80 443 443/udp
