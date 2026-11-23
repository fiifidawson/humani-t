"""Create the two database roles on Render, where no Postgres init script runs.

The same roles and grants as infrastructure/postgres/init/01-roles.sh, made with the
user Render creates. Safe to run before every deploy: it creates what is missing and
resets the two passwords to the ones Render holds.
"""

from __future__ import annotations

import asyncio
import os

import asyncpg

_ROLES = (
    ("humani_t_migrator", "DB_MIGRATOR_PASSWORD"),
    ("humani_t_app", "DB_APP_PASSWORD"),
)

_GRANTS = """
REVOKE ALL ON DATABASE {db} FROM PUBLIC;
GRANT CONNECT ON DATABASE {db} TO humani_t_migrator, humani_t_app;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO humani_t_migrator;
GRANT USAGE ON SCHEMA public TO humani_t_app;
ALTER DEFAULT PRIVILEGES FOR ROLE humani_t_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO humani_t_app;
ALTER DEFAULT PRIVILEGES FOR ROLE humani_t_migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO humani_t_app;
"""

_NO_CREATEROLE = (
    "The Render database user may not create roles. Set DB_SEPARATE_ROLES to false "
    "on the service to run with that one user (see docs/deploy/render.md)."
)


async def _create_or_reset(connection: asyncpg.Connection, role: str, password: str) -> None:
    exists = await connection.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", role)
    verb = "ALTER" if exists else "CREATE"
    # Role statements take no bind parameters; format() quotes them server-side.
    statement = await connection.fetchval(
        "SELECT format('%s ROLE %I LOGIN PASSWORD %L', $1::text, $2::text, $3::text)",
        verb,
        role,
        password,
    )
    await connection.execute(statement)


async def main() -> None:
    connection = await asyncpg.connect(
        host=os.environ["PG_HOST"],
        port=int(os.environ["PG_PORT"]),
        user=os.environ["PG_ADMIN_USER"],
        password=os.environ["PG_ADMIN_PASSWORD"],
        database=os.environ["PG_DATABASE"],
    )
    try:
        async with connection.transaction():
            for role, variable in _ROLES:
                await _create_or_reset(connection, role, os.environ[variable])
            # Changing the schema's owner and its default privileges needs membership.
            await connection.execute(
                "GRANT humani_t_migrator TO CURRENT_USER WITH INHERIT TRUE, SET TRUE"
            )
            database = await connection.fetchval("SELECT quote_ident(current_database())")
            await connection.execute(_GRANTS.format(db=database))
    except asyncpg.InsufficientPrivilegeError as exc:
        raise SystemExit(f"{_NO_CREATEROLE}\n{exc}") from exc
    finally:
        await connection.close()
    print("database roles ready: humani_t_migrator, humani_t_app")


if __name__ == "__main__":
    asyncio.run(main())
