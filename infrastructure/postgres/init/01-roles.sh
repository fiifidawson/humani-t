#!/bin/sh
# Runs once, when the Postgres volume is first created.
#
# Two roles, so a compromised app process cannot change the schema:
#   humani_t_migrator  owns the schema; used only by the one-shot migrate job
#   humani_t_app       reads and writes rows; cannot CREATE, ALTER or DROP
#
# Two databases with identical grants:
#   humani_t       development data
#   humani_t_test  for the Postgres contract tests, which delete what they write;
#                  kept separate so a test run never touches development data
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v migrator_password="$(cat /run/secrets/db_migrator_password)" \
  -v app_password="$(cat /run/secrets/db_app_password)" <<'SQL'
CREATE ROLE humani_t_migrator LOGIN PASSWORD :'migrator_password';
CREATE ROLE humani_t_app LOGIN PASSWORD :'app_password';
CREATE DATABASE humani_t_test;
SQL

for db in "$POSTGRES_DB" humani_t_test; do
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$db" -v db="$db" <<'SQL'
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db" TO humani_t_migrator, humani_t_app;

REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO humani_t_migrator;
GRANT USAGE ON SCHEMA public TO humani_t_app;

-- Tables the migrator creates later are usable by the app, but only for rows.
-- Append-only tables (the audit log) revoke UPDATE and DELETE in their migration.
ALTER DEFAULT PRIVILEGES FOR ROLE humani_t_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO humani_t_app;
ALTER DEFAULT PRIVILEGES FOR ROLE humani_t_migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO humani_t_app;
SQL
done
