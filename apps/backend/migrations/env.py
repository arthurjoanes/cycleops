"""Alembic environment: async psycopg, connected as cycleops_migrator, which then owns
every table it creates."""

import os

from alembic import context
from sqlalchemy import Connection, NullPool
from sqlalchemy.ext.asyncio import create_async_engine

from cycleops import aio
from cycleops.adapters.postgres.connect import async_url
from cycleops.adapters.postgres.tables import metadata


def _migrate(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run() -> None:
    url = context.config.attributes.get("url") or os.environ["MIGRATOR_DATABASE_URL"]
    engine = create_async_engine(async_url(url), poolclass=NullPool, hide_parameters=True)
    try:
        async with engine.connect() as conn:
            await conn.run_sync(_migrate)
    finally:
        await engine.dispose()


aio.run(_run())
