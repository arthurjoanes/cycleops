"""Canonical JSON and the hashes built on it.

Sorted keys, compact separators, ensure_ascii=False, strings in NFC, instants as integer
epoch seconds. The caller sorts station lists by stationId before hashing.
"""

import dataclasses
import hashlib
import json
import unicodedata
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel

HASH_VERSION = 1


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _plain(value.model_dump(by_alias=True))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _plain(dataclasses.asdict(value))
    if isinstance(value, datetime):
        return int(value.timestamp())
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        return {_plain(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(_plain(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        _plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def sha256_hex(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()
