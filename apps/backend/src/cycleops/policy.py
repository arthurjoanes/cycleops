"""Policy files under policy/, validated on load.

The YAML holds literal values only. Formulas live in code and read these fields.
"""

import os
from datetime import date
from pathlib import Path
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


def _check_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown timezone {value!r}") from exc
    return value


Timezone = Annotated[str, AfterValidator(_check_timezone)]
# Quoted in the YAML: unquoted 1:00 is a YAML 1.1 base-60 integer (60).
HourMinute = Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]
PositiveSeconds = Annotated[float, Field(gt=0)]


class _PolicyModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, validate_by_name=True, extra="forbid", frozen=True
    )


class HttpTimeouts(_PolicyModel):
    connect: PositiveSeconds
    read: PositiveSeconds
    write: PositiveSeconds
    pool: PositiveSeconds


class CollectionConfig(_PolicyModel):
    poll_interval_floor_seconds: Annotated[int, Field(gt=0)]
    publication_margin_seconds: Annotated[int, Field(ge=0)]
    http_timeouts: HttpTimeouts
    request_deadline_seconds: PositiveSeconds
    catalog_refresh_seconds: Annotated[int, Field(gt=0)]
    discovery_refresh_seconds: Annotated[int, Field(gt=0)]
    breaker_failures: Annotated[int, Field(gt=0)]
    breaker_open_seconds: Annotated[int, Field(gt=0)]
    quarantine_probe_seconds: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def _deadline_below_floor(self) -> Self:
        # A request must finish before the next one is due, or polls would overlap.
        if self.request_deadline_seconds >= self.poll_interval_floor_seconds:
            raise ValueError("requestDeadlineSeconds must be below pollIntervalFloorSeconds")
        return self


class ServiceHours(_PolicyModel):
    opens: HourMinute
    closes: HourMinute
    timezone: Timezone
    source: str
    checked_at: date


class ProviderConfig(_PolicyModel):
    system_id: Annotated[str, Field(pattern=r"^[a-z0-9-]{1,64}$")]
    timezone: Timezone
    station_report_semantics: Literal["feed_generation", "per_station"]
    service_hours: ServiceHours | Literal["always"]
    collection: CollectionConfig


class PriorityPolicy(_PolicyModel):
    version: Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]
    source_late_after_seconds: Annotated[int, Field(gt=0)]
    source_stale_after_seconds: Annotated[int, Field(gt=0)]
    fresh_again_after_publications: Annotated[int, Field(gt=0)]
    clock_skew_tolerance_seconds: Annotated[int, Field(ge=0)]
    confirm_min_span_seconds: Annotated[int, Field(ge=0)]
    recovery_min_span_seconds: Annotated[int, Field(ge=0)]
    recovery_min_fraction: Annotated[float, Field(gt=0, le=1)]
    recovery_min_floor: Annotated[int, Field(gt=0)]
    recovery_min_cap: Annotated[int, Field(gt=0)]
    station_stale_after_seconds: Annotated[int, Field(gt=0)]
    tick_interval_seconds: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def _ordering(self) -> Self:
        if self.source_stale_after_seconds <= self.source_late_after_seconds:
            raise ValueError("sourceStaleAfterSeconds must be above sourceLateAfterSeconds")
        if self.recovery_min_floor > self.recovery_min_cap:
            raise ValueError("recoveryMinFloor must not exceed recoveryMinCap")
        return self


def policy_dir() -> Path:
    return Path(os.environ.get("CYCLEOPS_POLICY_DIR", "policy"))


def _load_yaml(path: Path) -> object:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_provider(name: str = "oslo", directory: Path | None = None) -> ProviderConfig:
    path = (directory or policy_dir()) / f"provider-{name}.yaml"
    return ProviderConfig.model_validate(_load_yaml(path))


def load_priority_policy(directory: Path | None = None) -> PriorityPolicy:
    path = (directory or policy_dir()) / "priority-policy.yaml"
    return PriorityPolicy.model_validate(_load_yaml(path))
