"""JSON logs for api, worker and migrate: one line per record.

Fields: time, level, logger, message, plus whatever the call passes in `extra`
(streamId, sequence, outcome, duration_ms...). Callers never pass payloads.
"""

import json
import logging
import os
from datetime import UTC, datetime

from cycleops.contracts import iso

# Attributes every LogRecord has; anything else on a record came from `extra`.
_RESERVED = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message",
    "asctime",
    # uvicorn's copy of the message with ANSI colors.
    "color_message",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, object] = {
            "time": iso(datetime.fromtimestamp(record.created, UTC)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        line.update((k, v) for k, v in vars(record).items() if k not in _RESERVED)
        if record.exc_info and record.exc_info[0] is not None:
            line["error"] = record.exc_info[0].__name__
            line["traceback"] = self.formatException(record.exc_info)
        # json.dumps escapes newlines, so a traceback stays on the same line.
        return json.dumps(line, ensure_ascii=False, default=str)


def setup(level: str | None = None) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    level = level or os.environ.get("CYCLEOPS_LOG_LEVEL", "INFO")
    logging.basicConfig(level=level.upper(), handlers=[handler], force=True)
