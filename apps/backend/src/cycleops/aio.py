"""asyncio entry point for code that may run on the Windows host (CLIs, tests)."""

import asyncio
import sys
from collections.abc import Coroutine
from typing import Any


def run[T](main: Coroutine[Any, Any, T]) -> T:
    # psycopg's async mode does not work on the Proactor loop, the Windows default.
    # Containers are Linux and keep the default loop.
    factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    return asyncio.run(main, loop_factory=factory)
