import json
import logging
import sys
from collections.abc import Iterator
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, text
from sqlalchemy.engine import make_url

from cycleops import aio
from cycleops.adapters.postgres.connect import create_engine
from cycleops.adapters.postgres.tables import metadata
from cycleops.cli import migrate
from cycleops.jsonlog import JsonFormatter
from integration.conftest import PASSWORDS, Database


@pytest.fixture
def restore_logging() -> Iterator[None]:
    """migrate.main() installs the JSON handler on the root logger."""
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def records(err: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in err.splitlines() if line.strip()]


@pytest.mark.usefixtures("restore_logging")
def test_migrate_twice_is_safe_and_logs_json(
    database: Database, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name, value in database.env().items():
        monkeypatch.setenv(name, value)
    assert migrate.main([]) == 0
    assert migrate.main([]) == 0
    err = capsys.readouterr().err
    lines = records(err)
    done = [(r["message"], r.get("step")) for r in lines if r["logger"] == "cycleops.migrate"]
    assert done == [("roles ready", 1), ("schema at head", 2)] * 2
    assert all({"time", "level", "logger", "message"} <= r.keys() for r in lines)
    assert not any(password in err for password in PASSWORDS.values())


@pytest.mark.usefixtures("restore_logging")
def test_migrate_exit_codes(
    database: Database, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name, value in database.env().items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("CYCLEOPS_API_DB_PASSWORD")
    assert migrate.main([]) == 2
    missing = records(capsys.readouterr().err)[-1]
    assert missing["variables"] == ["CYCLEOPS_API_DB_PASSWORD"]

    monkeypatch.setenv("CYCLEOPS_API_DB_PASSWORD", PASSWORDS["cycleops_api"])
    wrong = make_url(database.superuser_url).set(password="wrong-password-xyz")
    monkeypatch.setenv("SUPERUSER_DATABASE_URL", wrong.render_as_string(hide_password=False))
    assert migrate.main([]) == 1
    err = capsys.readouterr().err
    failed = records(err)[-1]
    assert (failed["message"], failed["step"], failed["error"]) == (
        "migrate failed",
        1,
        "OperationalError",
    )
    assert "wrong-password-xyz" not in err


def test_the_migration_creates_exactly_the_tables(database: Database) -> None:
    def diff(sync: Connection) -> list[Any]:
        return compare_metadata(MigrationContext.configure(sync), metadata)  # type: ignore[no-any-return]

    async def go() -> tuple[list[Any], str]:
        engine = create_engine(database.migrator_url)
        async with engine.connect() as conn:
            found = await conn.run_sync(diff)
            version = (
                await conn.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one()
        await engine.dispose()
        return found, version

    found, version = aio.run(go())
    assert found == []
    head = ScriptDirectory.from_config(Config(str(migrate.ALEMBIC_INI))).get_current_head()
    assert version == head == "0003"


def test_json_log_line() -> None:
    formatter = JsonFormatter()
    record = logging.makeLogRecord(
        {
            "name": "cycleops.worker",
            "levelname": "WARNING",
            "levelno": logging.WARNING,
            "msg": "attempt %s",
            "args": ("failed",),
            "streamId": "synthetic-oslo:0badcafe",
            "sequence": 3,
        }
    )
    line = json.loads(formatter.format(record))
    assert line == {
        "time": line["time"],
        "level": "WARNING",
        "logger": "cycleops.worker",
        "message": "attempt failed",
        "streamId": "synthetic-oslo:0badcafe",
        "sequence": 3,
    }
    try:
        raise ValueError("two\nlines")
    except ValueError:
        record = logging.makeLogRecord({"msg": "boom", "exc_info": sys.exc_info()})
    text_line = formatter.format(record)
    assert "\n" not in text_line
    assert json.loads(text_line)["error"] == "ValueError"
