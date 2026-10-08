"""Engines and the collector's lock connection.

URLs come as postgresql://... (compose, .env); SQLAlchemy gets postgresql+psycopg://.
"""

from sqlalchemy import NullPool, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

# pg advisory lock key of the collector: b"cycleops" as a big-endian int64.
LOCK_KEY = int.from_bytes(b"cycleops")
# pg_notify channel; the payload is the cursor "<streamId>@<sequence>".
NOTIFY_CHANNEL = "cycleops_events"

# Client side: the worker notices a dead server. Server side: PostgreSQL drops the session
# of a frozen worker and frees the lock.
KEEPALIVES = {
    "keepalives": 1,
    "keepalives_idle": 10,
    "keepalives_interval": 5,
    "keepalives_count": 3,
    "tcp_user_timeout": 20000,
    "options": (
        "-c tcp_keepalives_idle=10 -c tcp_keepalives_interval=5 "
        "-c tcp_keepalives_count=3 -c tcp_user_timeout=20000"
    ),
}


def async_url(url: str) -> str:
    return make_url(url).set(drivername="postgresql+psycopg").render_as_string(hide_password=False)


def create_engine(
    url: str, *, pool_size: int = 5, max_overflow: int = 5, pool_timeout: float = 30
) -> AsyncEngine:
    # hide_parameters: statement errors in the logs never carry payloads or values.
    return create_async_engine(
        async_url(url),
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_pre_ping=True,
        hide_parameters=True,
        # Without it a connect to an unreachable host can hang past pool_timeout.
        connect_args={"connect_timeout": 10},
    )


async def connect_lock(url: str) -> AsyncConnection:
    """A dedicated connection, outside any pool: the advisory lock lives and dies with it.
    Close it to end the leadership."""
    engine = create_async_engine(
        async_url(url),
        poolclass=NullPool,
        hide_parameters=True,
        connect_args=KEEPALIVES | {"connect_timeout": 10},
    )
    return await engine.connect()


async def try_lock(conn: AsyncConnection) -> bool:
    """Session-level lock: it survives the commit and ends with the connection."""
    acquired = (
        await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY})
    ).scalar()
    await conn.commit()
    return bool(acquired)


async def unlock(conn: AsyncConnection) -> None:
    await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
    await conn.commit()
