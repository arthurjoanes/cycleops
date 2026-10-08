"""Service hours in the provider's time zone, DST included."""

from datetime import UTC, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from cycleops.policy import ServiceHours

Hours = ServiceHours | Literal["always"]
Boundary = Literal["close", "open"]


def in_service(instant: datetime, hours: Hours) -> bool:
    """[opens, closes) in local time; closes may be past midnight."""
    if hours == "always":
        return True
    opens, closes = time.fromisoformat(hours.opens), time.fromisoformat(hours.closes)
    local = instant.astimezone(ZoneInfo(hours.timezone)).time()
    if opens == closes:
        return True
    if opens < closes:
        return opens <= local < closes
    return local >= opens or local < closes


def boundaries(after: datetime, until: datetime, hours: Hours) -> list[tuple[datetime, Boundary]]:
    """Closings and openings in (after, until], in time order. When the span covers several
    days only the last closing and the last opening count."""
    if hours == "always" or until <= after:
        return []
    tz = ZoneInfo(hours.timezone)
    first = after.astimezone(tz).date() - timedelta(days=1)
    last = until.astimezone(tz).date() + timedelta(days=1)
    pairs: list[tuple[Boundary, time]] = [
        ("close", time.fromisoformat(hours.closes)),
        ("open", time.fromisoformat(hours.opens)),
    ]
    latest: dict[Boundary, datetime] = {}
    day = first
    while day <= last:
        for kind, at in pairs:
            instant = datetime.combine(day, at, tz).astimezone(UTC)
            if after < instant <= until:
                latest[kind] = max(instant, latest.get(kind, instant))
        day += timedelta(days=1)
    return sorted(((at, kind) for kind, at in latest.items()), key=lambda item: item[0])
