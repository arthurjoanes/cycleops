"""migrate: step 1 creates the roles as the superuser, step 2 runs Alembic as
cycleops_migrator.

Safe to run again: roles are created only when missing, and the passwords are set on every
run so a changed .env applies. Exit codes: 0 done, 1 database error, 2 configuration.
"""

import argparse
import logging
import os
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy.exc import SQLAlchemyError

from cycleops import aio, jsonlog

log = logging.getLogger("cycleops.migrate")

PASSWORD_ENV = {
    "cycleops_migrator": "CYCLEOPS_MIGRATOR_DB_PASSWORD",
    "cycleops_worker": "CYCLEOPS_WORKER_DB_PASSWORD",
    "cycleops_api": "CYCLEOPS_API_DB_PASSWORD",
}
URL_ENV = ("SUPERUSER_DATABASE_URL", "MIGRATOR_DATABASE_URL")
# apps/backend/alembic.ini; the image keeps apps/backend as is.
ALEMBIC_INI = Path(__file__).resolve().parents[3] / "alembic.ini"


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(prog="migrate", description=__doc__).parse_args(argv)
    jsonlog.setup()
    missing = [name for name in (*URL_ENV, *PASSWORD_ENV.values()) if not os.environ.get(name)]
    if missing:
        log.error("missing environment variables", extra={"variables": missing})
        return 2
    passwords = {role: os.environ[name] for role, name in PASSWORD_ENV.items()}
    step = 1
    try:
        aio.run(create_roles(os.environ["SUPERUSER_DATABASE_URL"], passwords))
        log.info("roles ready", extra={"step": step, "roles": list(PASSWORD_ENV)})
        step = 2
        upgrade(os.environ["MIGRATOR_DATABASE_URL"])
        log.info("schema at head", extra={"step": step})
    except (psycopg.Error, SQLAlchemyError, OSError) as exc:
        log.error("migrate failed", extra={"step": step}, exc_info=exc)
        return 1
    return 0


async def create_roles(superuser_url: str, passwords: dict[str, str]) -> None:
    """Step 1, on the target database, as the superuser."""
    async with await psycopg.AsyncConnection.connect(superuser_url, autocommit=True) as conn:
        for role, password in passwords.items():
            # The pg_roles check skips the usual case; the handler covers two runs at once.
            await conn.execute(
                sql.SQL(
                    "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = {name}) "
                    "THEN CREATE ROLE {role} LOGIN; END IF; "
                    "EXCEPTION WHEN duplicate_object THEN NULL; END $$"
                ).format(name=sql.Literal(role), role=sql.Identifier(role))
            )
            # The SCRAM verifier is computed here, so the plain password never reaches the
            # server (nor its statement log).
            verifier = conn.pgconn.encrypt_password(
                password.encode(), role.encode(), b"scram-sha-256"
            )
            await conn.execute(
                sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(verifier.decode())
                )
            )
        row = await (await conn.execute("SELECT current_database()")).fetchone()
        assert row is not None
        db = sql.Identifier(row[0])
        roles = sql.SQL(", ").join(sql.Identifier(r) for r in passwords)
        # Owner of the database, so also of its public schema (pg_database_owner).
        await conn.execute(sql.SQL("ALTER DATABASE {} OWNER TO cycleops_migrator").format(db))
        await conn.execute(sql.SQL("REVOKE CONNECT ON DATABASE {} FROM PUBLIC").format(db))
        await conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(db, roles))
        # The quota counts the free space the purge's VACUUM leaves in the files as
        # reusable. Reading the free space map takes this extension (superuser only) and
        # pg_stat_scan_tables.
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pg_freespacemap SCHEMA public")
        await conn.execute("GRANT pg_stat_scan_tables TO cycleops_worker")


def upgrade(migrator_url: str) -> None:
    """Step 2: alembic upgrade head, connected as cycleops_migrator."""
    config = Config(str(ALEMBIC_INI))
    # Through attributes, not the ini: ConfigParser would read a % in the password.
    config.attributes["url"] = migrator_url
    command.upgrade(config, "head")
