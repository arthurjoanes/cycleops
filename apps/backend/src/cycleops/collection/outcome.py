"""Classification of one attempt. Pure, so recorded attempts classify the same way."""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Literal

from cycleops.adapters.gbfs import (
    ContractError,
    Envelope,
    InfoPublication,
    ReportSemantics,
    StatusPublication,
    SystemInformation,
    parse_discovery,
    parse_envelope,
    parse_station_information,
    parse_station_status,
    parse_system_information,
    parse_vehicle_types,
)
from cycleops.collection.client import FEEDS, Attempt, DisallowedUrlError, check_url

ErrorClass = Literal["transport", "contract"]
Publication = StatusPublication | InfoPublication | SystemInformation | dict[str, str] | None

# Failures that happen before a full body exists. too_large is ours, so it is a contract
# error; the others count toward the breaker.
TRANSPORT_ERRORS = frozenset({"timeout", "network_error", "truncated"})


@dataclass(frozen=True)
class Outcome:
    # ok | not_modified | timeout | network_error | truncated | too_large | http_error | invalid
    code: str
    error_class: ErrorClass | None
    status: int | None
    detail: str | None = None
    envelope: Envelope | None = None
    # Adapter result for ok: StatusPublication, InfoPublication, SystemInformation, or the
    # feed name -> URL map of gbfs.json.
    publication: Publication = None
    retry_after_until: datetime | None = None

    @property
    def ok(self) -> bool:
        return self.code == "ok"

    def fingerprint(self, feed: str) -> str:
        key = f"{feed}|{self.code}|{self.status}|{self.detail}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]


def classify_attempt(attempt: Attempt, semantics: ReportSemantics) -> Outcome:
    return classify(
        attempt.feed,
        attempt.status,
        attempt.error,
        attempt.body_json,
        attempt.headers,
        attempt.received_at,
        semantics,
    )


def classify(
    feed: str,
    status: int | None,
    error: str | None,
    body: Any,
    headers: dict[str, str],
    received_at: datetime,
    semantics: ReportSemantics,
) -> Outcome:
    if error in TRANSPORT_ERRORS:
        return Outcome(error, "transport", status)
    if error == "too_large":
        return Outcome("too_large", "contract", status)
    if status is None:
        return Outcome("network_error", "transport", None)
    if status == 304:
        return Outcome("not_modified", None, status)
    if status == 429:
        until = _retry_after(headers.get("retry-after"), received_at)
        return Outcome("http_error", "transport", status, "status 429", retry_after_until=until)
    if status >= 500:
        return Outcome("http_error", "transport", status, f"status {status}")
    if status != 200:
        # Redirects land here too: they are never followed.
        return Outcome("http_error", "contract", status, f"status {status}")
    if body is None:
        return Outcome("invalid", "contract", status, "body is not JSON")
    try:
        envelope, publication = _parse(feed, body, semantics)
    except ContractError as exc:
        return Outcome("invalid", "contract", status, str(exc))
    return Outcome("ok", None, status, envelope=envelope, publication=publication)


def _parse(feed: str, body: Any, semantics: ReportSemantics) -> tuple[Envelope, Publication]:
    if feed == "station_status":
        status = parse_station_status(body, semantics)
        return status.envelope, status
    if feed == "station_information":
        info = parse_station_information(body)
        return info.envelope, info
    if feed == "system_information":
        system = parse_system_information(body)
        return system.envelope, system
    if feed == "vehicle_types":
        return parse_vehicle_types(body), None
    if feed == "gbfs":
        envelope, urls = parse_discovery(body)
        used = {name: url for name, url in urls.items() if name in FEEDS}
        for name, url in used.items():
            try:
                check_url(name, url)
            except DisallowedUrlError as exc:
                raise ContractError(str(exc)) from exc
        return envelope, used
    return parse_envelope(body)[0], None


def _retry_after(value: str | None, received_at: datetime) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    # A header must never crash the loop: huge or odd values are ignored.
    try:
        if value.isdigit():
            return received_at + timedelta(seconds=int(value))
        when = parsedate_to_datetime(value)
    except TypeError, ValueError, OverflowError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)
