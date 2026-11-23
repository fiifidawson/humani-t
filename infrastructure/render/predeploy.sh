#!/bin/bash
# Render's pre-deploy step: make sure the two database roles exist, then migrate.
# Runs before each deploy on separate compute, with no access to the disk.
set -euo pipefail
source /render/env.sh

if [ "${DB_SEPARATE_ROLES:-true}" = "true" ]; then
	python /render/create_roles.py
fi

cd /app
# As compose's migrate job: Alembic needs only the schema owner's URL.
HUMANI_T_ENVIRONMENT=development HUMANI_T_MIGRATION_DATABASE_URL="$MIGRATION_URL" \
	alembic upgrade head
