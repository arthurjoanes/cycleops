"""SIGTERM with open streams, against `python -m cycleops.api` as a real process.

On Windows there is no SIGTERM to deliver: CTRL_BREAK reaches the same uvicorn handler
(SIGBREAK), so the path under test is the same."""

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import httpx

from cycleops import aio
from cycleops.api.__main__ import GRACEFUL_TIMEOUT_SECONDS
from integration.conftest import Database
from integration.feed import catalog, publication, worker
from integration.test_api import STREAM_PATH, opened


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def stop(proc: subprocess.Popen[bytes]) -> None:
    if sys.platform == "win32":
        proc.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        proc.send_signal(signal.SIGTERM)


def test_sigterm_sends_stream_closed_before_the_graceful_timeout(
    clean_database: Database, tmp_path: Path
) -> None:
    port = free_port()
    env = {**os.environ, "DATABASE_URL": clean_database.api_url, "CYCLEOPS_LOG_LEVEL": "INFO"}
    env.pop("FORWARDED_ALLOW_IPS", None)
    command = [sys.executable, "-m", "cycleops.api", "--host", "127.0.0.1", "--port", str(port)]
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    log_path = tmp_path / "api.log"

    async def go(proc: subprocess.Popen[bytes]) -> tuple[list[tuple[str, Any, str]], float]:
        async with (
            httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=10) as http,
            worker(clean_database.worker_url) as (conn, ingestor),
            AsyncExitStack() as stack,
        ):
            deadline = time.monotonic() + 30
            while True:
                assert proc.poll() is None, log_path.read_text()
                try:
                    if (await http.get("/health/ready")).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                assert time.monotonic() < deadline, log_path.read_text()
                await asyncio.sleep(0.2)
            await ingestor.process(conn, *catalog())
            cursor = (await http.get("/api/v1/stations/snapshot")).json()["cursor"]
            sses = [await stack.enter_async_context(opened(http, cursor)) for _ in range(2)]
            await ingestor.process(conn, *publication(1, {"100": 2, "200": 3}))
            for sse in sses:
                assert await sse.batches(1) == [2]
            started = time.monotonic()
            stop(proc)
            closed = []
            for sse in sses:
                m = await sse.next()
                assert m is not None and "id" not in m, m
                closed.append((m["event"], json.loads(m["data"]), m["retry"]))
                assert await sse.next() is None
            return closed, time.monotonic() - started

    with log_path.open("wb") as log_file:
        proc = subprocess.Popen(
            command, env=env, stdout=log_file, stderr=subprocess.STDOUT, creationflags=flags
        )
        try:
            closed, elapsed = aio.run(go(proc))
            proc.wait(timeout=GRACEFUL_TIMEOUT_SECONDS + 5)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    assert closed == [("stream_closed", {"reason": "shutdown"}, "2000")] * 2
    assert elapsed < GRACEFUL_TIMEOUT_SECONDS
    # Every line the process wrote is JSON; the streams ended by the shutdown are logged.
    lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    streams = [entry for entry in lines if entry.get("path") == STREAM_PATH]
    assert [(entry["status"], entry["outcome"]) for entry in streams] == [(200, "shutdown")] * 2
