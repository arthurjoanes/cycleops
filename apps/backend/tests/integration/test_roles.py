import psycopg
import pytest

from cycleops import aio
from cycleops.adapters.postgres.tables import metadata
from integration.conftest import Database

WRITES = [
    "INSERT INTO event_log VALUES ('s', 1, 'station_batch', '{}', now())",
    "UPDATE event_log SET event_type = 'x'",
    "DELETE FROM event_log",
    "TRUNCATE event_log",
]


async def scalar(url: str, statement: str) -> object:
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as conn:
        row = await (await conn.execute(statement)).fetchone()
        return row[0] if row else None


def test_api_role_only_reads(clean_database: Database) -> None:
    async def go() -> None:
        async with await psycopg.AsyncConnection.connect(
            clean_database.api_url, autocommit=True
        ) as conn:
            for table in metadata.tables:
                await conn.execute(f"SELECT * FROM {table}")
            for statement in WRITES:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    await conn.execute(statement)

    aio.run(go())


def test_worker_writes_rows_but_not_the_schema(clean_database: Database) -> None:
    async def go() -> None:
        async with await psycopg.AsyncConnection.connect(
            clean_database.worker_url, autocommit=True
        ) as conn:
            for statement in WRITES[:3]:
                await conn.execute(statement)
            for statement in ("CREATE TABLE x (a int)", "TRUNCATE event_log"):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    await conn.execute(statement)

    aio.run(go())


def test_ownership_connect_and_default_privileges(database: Database) -> None:
    def value(statement: str) -> object:
        return aio.run(scalar(database.superuser_url, statement))

    tables = "SELECT array_agg(DISTINCT tableowner) FROM pg_tables WHERE schemaname = 'public'"
    assert value(tables) == ["cycleops_migrator"]
    owner = "SELECT datdba::regrole::text FROM pg_database WHERE datname = current_database()"
    assert value(owner) == "cycleops_migrator"
    public = "SELECT has_database_privilege('public', current_database(), 'CONNECT')"
    assert value(public) is False

    async def later_table() -> None:
        # A table a later migration creates gets the same grants.
        async with await psycopg.AsyncConnection.connect(
            database.migrator_url, autocommit=True
        ) as conn:
            await conn.execute("CREATE TABLE later (a int)")
        try:
            async with await psycopg.AsyncConnection.connect(
                database.worker_url, autocommit=True
            ) as conn:
                await conn.execute("INSERT INTO later VALUES (1)")
            async with await psycopg.AsyncConnection.connect(
                database.api_url, autocommit=True
            ) as conn:
                await conn.execute("SELECT * FROM later")
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    await conn.execute("DELETE FROM later")
        finally:
            async with await psycopg.AsyncConnection.connect(
                database.migrator_url, autocommit=True
            ) as conn:
                await conn.execute("DROP TABLE later")

    aio.run(later_table())
