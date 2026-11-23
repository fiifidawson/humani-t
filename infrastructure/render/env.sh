# Build the app's settings from what Render provides. Sourced by the other scripts.
#
# Render cannot join values into a URL, so the database URLs are made here from
# the database's parts and the passwords Render generated for the two roles.
set -euo pipefail

_database_url() {
	python - "$@" <<'PY'
import sys
from urllib.parse import quote

user, password, host, port, database = sys.argv[1:]
print(f"postgresql+asyncpg://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{port}/{database}")
PY
}

if [ "${DB_SEPARATE_ROLES:-true}" = "true" ]; then
	HUMANI_T_DATABASE_URL="$(_database_url humani_t_app "$DB_APP_PASSWORD" "$PG_HOST" "$PG_PORT" "$PG_DATABASE")"
	MIGRATION_URL="$(_database_url humani_t_migrator "$DB_MIGRATOR_PASSWORD" "$PG_HOST" "$PG_PORT" "$PG_DATABASE")"
else
	HUMANI_T_DATABASE_URL="$(_database_url "$PG_ADMIN_USER" "$PG_ADMIN_PASSWORD" "$PG_HOST" "$PG_PORT" "$PG_DATABASE")"
	MIGRATION_URL="$HUMANI_T_DATABASE_URL"
fi
export HUMANI_T_DATABASE_URL MIGRATION_URL
# Render sets RENDER_EXTERNAL_URL to the service's https://….onrender.com address.
export HUMANI_T_PUBLIC_ORIGIN="${PUBLIC_ORIGIN:-$RENDER_EXTERNAL_URL}"
