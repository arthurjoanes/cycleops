from datetime import timedelta

from sqlalchemy import insert

from cycleops import aio
from cycleops.adapters.postgres import queries
from cycleops.adapters.postgres.connect import create_engine
from cycleops.adapters.postgres.tables import feed_publications
from cycleops.adapters.synthetic import SyntheticConfig, SyntheticFeed, collect
from cycleops.application.ingest import publication_row
from cycleops.application.replay import compile_synthetic, engine_outcome, entries
from cycleops.contracts import FeedDiagnostics
from integration.conftest import Database
from integration.feed import POLICY, at, catalog, publication, worker


def test_diagnostics_read_with_select_only_role_filters_window_and_stream(
    clean_database: Database,
) -> None:
    async def go() -> tuple[FeedDiagnostics | None, FeedDiagnostics | None, bool]:
        async with worker(clean_database.worker_url) as (conn, ingestor):
            await ingestor.process(conn, *catalog())
            await ingestor.process(conn, *publication(1, {"100": 2, "200": 3}))
            await ingestor.process(conn, *publication(1, {"100": 2, "200": 3}, seconds=20))
            _, attempt, outcome = publication(2, {"100": 0, "200": 0})
            await conn.execute(
                insert(feed_publications).values(
                    publication_row("another-stream", attempt, outcome)
                )
            )
            await conn.commit()
        engine = create_engine(clean_database.api_url)
        async with engine.connect() as reader:
            found = await queries.feed_diagnostics(reader, at(0), at(60))
            narrow = await queries.feed_diagnostics(reader, at(20), at(21))
            open_transaction = reader.in_transaction()
        await engine.dispose()
        return found, narrow, open_transaction

    found, narrow, open_transaction = aio.run(go())
    assert found is not None and narrow is not None and not open_transaction
    assert found.totals.attempts == 3 and found.totals.accepted_publications == 2
    assert found.totals.duplicate_publications == found.totals.rejected_publications == 1
    assert narrow.totals.attempts == 1 and narrow.totals.accepted_publications == 0
    assert narrow.totals.duplicate_publications == 1
    assert narrow.observed_start == narrow.observed_end == at(20.3)


def test_replay_diagnostics_match_attempts_persisted_by_worker(clean_database: Database) -> None:
    cfg = SyntheticConfig(
        seed=1,
        stations=10,
        cadence_seconds=15,
        change_fraction=0,
        start=at(0),
        end=at(0) + timedelta(minutes=20),
        service_hours="always",
        scenarios=["frozen_source"],
    )
    artifact = compile_synthetic(cfg, POLICY)
    attempts = collect(SyntheticFeed(cfg), POLICY)

    async def go() -> FeedDiagnostics | None:
        async with worker(clean_database.worker_url) as (conn, ingestor):
            # Resume entries only after process(), as the offline compiler resumes
            # after step(). The worker writes the engine's final outcome in that row.
            sorted_attempts = sorted(attempts, key=lambda a: a.received_at)
            for attempt, (_, entry, _) in zip(
                sorted_attempts, entries(attempts, POLICY), strict=True
            ):
                await ingestor.process(conn, entry, attempt, engine_outcome(attempt, POLICY))
        engine = create_engine(clean_database.api_url)
        async with engine.connect() as reader:
            found = await queries.feed_diagnostics(reader, cfg.start, cfg.end)
        await engine.dispose()
        return found

    found = aio.run(go())
    assert found is not None
    assert found.totals == artifact.diagnostics.totals
    assert found.feeds == artifact.diagnostics.feeds
    assert found.totals.duplicate_publications > 0
