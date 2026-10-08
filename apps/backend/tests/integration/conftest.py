"""Integration tests against a real PostgreSQL.

CYCLEOPS_TEST_DATABASE_URL is a superuser URL of a disposable server:
    docker compose -p cycleops-test -f compose.test.yaml up -d --wait
    CYCLEOPS_TEST_DATABASE_URL=postgresql://postgres:local-only-test@127.0.0.1:55432/postgres
Without it every test here is skipped. The session gets a fresh database, migrated with
both steps of `migrate`; tests reach it through the role URLs of `database`.

Engines and connections belong to the event loop that made them, and each test runs its
own loop (cycleops.aio.run), so fixtures hand out URLs, never engines.
"""

import os
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from sqlalchemy.engine import make_url

from cycleops import aio
from cycleops.adapters.postgres.tables import metadata
from cycleops.cli import migrate

URL = os.environ.get("CYCLEOPS_TEST_DATABASE_URL")
HERE = Path(__file__).parent
PASSWORDS = {
    "cycleops_migrator": "local-only-migrator",
    "cycleops_worker": "local-only-worker",
    "cycleops_api": "local-only-api",
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if HERE in item.path.parents:
            item.add_marker(pytest.mark.integration)


@dataclass(frozen=True)
class Database:
    name: str
    superuser_url: str
    migrator_url: str
    worker_url: str
    api_url: str

    def env(self) -> dict[str, str]:
        """The environment `migrate` reads."""
        return {
            "SUPERUSER_DATABASE_URL": self.superuser_url,
            "MIGRATOR_DATABASE_URL": self.migrator_url,
            **{migrate.PASSWORD_ENV[role]: pw for role, pw in PASSWORDS.items()},
        }


def role_url(base: str, database: str, role: str | None = None) -> str:
    url = make_url(base).set(database=database)
    if role is not None:
        url = url.set(username=role, password=PASSWORDS[role])
    return url.render_as_string(hide_password=False)


async def admin(url: str, statement: sql.Composed) -> None:
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as conn:
        await conn.execute(statement)


@pytest.fixture(scope="session")
def database() -> Iterator[Database]:
    if not URL:
        pytest.skip("CYCLEOPS_TEST_DATABASE_URL is not set")
    name = f"cycleops_test_{secrets.token_hex(4)}"
    aio.run(admin(URL, sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name))))
    db = Database(
        name=name,
        superuser_url=role_url(URL, name),
        migrator_url=role_url(URL, name, "cycleops_migrator"),
        worker_url=role_url(URL, name, "cycleops_worker"),
        api_url=role_url(URL, name, "cycleops_api"),
    )
    try:
        aio.run(migrate.create_roles(db.superuser_url, PASSWORDS))
        migrate.upgrade(db.migrator_url)
        yield db
    finally:
        aio.run(admin(URL, sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name))))


@pytest.fixture
def clean_database(database: Database) -> Database:
    """The session database with every table emptied: one stream per test."""
    tables = sql.SQL(", ").join(sql.Identifier(t) for t in metadata.tables)
    aio.run(admin(database.superuser_url, sql.SQL("TRUNCATE {}").format(tables)))
    return database
