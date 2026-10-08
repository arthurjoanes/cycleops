"""python -m cycleops.api: the API under uvicorn, one process per container.

Shutdown order: SIGTERM sets the app's shutdown event, open streams send stream_closed
(shutdown) and end within SHUTDOWN_GRACE_SECONDS (5 s), uvicorn waits for connections up to
its graceful timeout (10 s), and Compose's stop_grace_period (20 s) comes last. The lifespan
shutdown only runs after the connections are gone, so it cannot be what closes streams.

A client that stops reading is reset after WRITE_TIMEOUT_SECONDS with bytes unsent (Protocol).

Exit codes: 2 configuration; otherwise uvicorn's.
"""

import argparse
import asyncio
import logging
import os
import socket
import struct
import sys
from types import FrameType

import uvicorn
from uvicorn.protocols.http.h11_impl import H11Protocol

from cycleops import aio, jsonlog
from cycleops.api.app import Settings, create_app

log = logging.getLogger("cycleops.api")

GRACEFUL_TIMEOUT_SECONDS = 10
# A bit over the heartbeat interval (15 s).
WRITE_TIMEOUT_SECONDS = 20
# l_onoff 1, l_linger 0: close() resets and drops the unsent bytes. Windows reads the first
# two shorts of this, which are the same values.
_RESET = struct.pack("ii", 1, 0)


class Protocol(H11Protocol):
    """uvicorn's h11 protocol, plus a write timeout. A client that stops reading keeps the
    writes paused; after `write_timeout` the connection is reset. uvicorn's close() would
    wait for the buffer to drain, which then never happens: the socket and its buffers
    would stay, outside the stream limits. Covers every write, the response's last one too."""

    write_timeout: float = WRITE_TIMEOUT_SECONDS
    _stalled: asyncio.TimerHandle | None = None

    def connection_made(self, transport: asyncio.Transport) -> None:  # type: ignore[override]
        super().connection_made(transport)
        # Any byte left unsent pauses the writes and starts the timer. Under asyncio's
        # 64 KiB high-water mark a stream could end with a few KiB stuck and no timer.
        transport.set_write_buffer_limits(high=0)

    def pause_writing(self) -> None:
        super().pause_writing()
        self._stalled = self.loop.call_later(self.write_timeout, self._reset)

    def resume_writing(self) -> None:
        super().resume_writing()
        self._unstall()

    def connection_lost(self, exc: Exception | None) -> None:
        self._unstall()
        super().connection_lost(exc)

    def _unstall(self) -> None:
        if self._stalled is not None:
            self._stalled.cancel()
            self._stalled = None

    def _reset(self) -> None:
        sock = self.transport.get_extra_info("socket")
        if sock is not None:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, _RESET)
        # connection_lost follows: the response sees a disconnect and its stream ends.
        self.transport.abort()


class Server(uvicorn.Server):
    """handle_exit is uvicorn's signal handler (SIGINT, SIGTERM; SIGBREAK on Windows)."""

    def __init__(self, config: uvicorn.Config, shutdown: asyncio.Event) -> None:
        super().__init__(config)
        self._shutdown = shutdown
        self._loop: asyncio.AbstractEventLoop | None = None

    async def serve(self, sockets: list[socket.socket] | None = None) -> None:
        self._loop = asyncio.get_running_loop()
        await super().serve(sockets)

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        # A signal handler interrupts the loop at any point: hand the event over to it.
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._shutdown.set)
        super().handle_exit(sig, frame)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cycleops.api", description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    jsonlog.setup()
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        log.error("invalid configuration: %s", exc)
        return 2
    # Proxy headers only from the edge: with "*" any client could pick its own IP for
    # the per-IP limits. Unset, uvicorn trusts loopback only.
    forwarded = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")
    if "*" in forwarded:
        log.error("invalid configuration: FORWARDED_ALLOW_IPS must list addresses, not *")
        return 2
    app = create_app(settings)
    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        http=Protocol,
        proxy_headers=True,
        forwarded_allow_ips=forwarded,
        timeout_graceful_shutdown=GRACEFUL_TIMEOUT_SECONDS,
        # Logging is ours (JSON, one access line per request with the request id).
        log_config=None,
        access_log=False,
        server_header=False,
    )
    aio.run(Server(config, app.state.shutdown).serve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
