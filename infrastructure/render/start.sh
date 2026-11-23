#!/bin/bash
# Start Caddy, the API and the worker; when any one stops, stop the rest so Render
# restarts the whole service rather than running it half alive.
set -euo pipefail
source /render/env.sh

documents=/var/lib/humani-t/documents
chown 10001:10001 "$documents"
chmod 0700 "$documents"

# The app needs none of these. Unset here, while still root, they cannot be read
# back from this process by the unprivileged ones below.
unset MIGRATION_URL DB_MIGRATOR_PASSWORD PG_ADMIN_USER PG_ADMIN_PASSWORD

# HOME too: asyncpg looks for client certificates under ~/.postgresql, and root's
# home is unreadable to uid 10001, which fails every database connection.
as_app() {
	setpriv --reuid=10001 --regid=10001 --clear-groups --no-new-privs -- \
		env HOME=/nonexistent "$@"
}

# HUMANI_T_DOMAIN goes to Caddy only: the API refuses HUMANI_T_ names it does not know.
domain="${PUBLIC_ORIGIN:-$RENDER_EXTERNAL_URL}"
HUMANI_T_DOMAIN="${domain#https://}" as_app caddy run --config /etc/caddy/Caddyfile &
as_app uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers \
	--no-server-header --timeout-graceful-shutdown 20 &
as_app python -m app.worker &

trap 'kill -TERM $(jobs -p) 2>/dev/null || true' TERM INT
status=0
wait -n || status=$?
kill -TERM $(jobs -p) 2>/dev/null || true
wait || true
exit "$status"
