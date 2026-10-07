from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import POLICY_DIR
from pydantic import ValidationError

from cycleops.policy import (
    PriorityPolicy,
    ProviderConfig,
    load_priority_policy,
    load_provider,
)


def raw(name: str) -> dict[str, Any]:
    data = yaml.safe_load((POLICY_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_policy_files_load() -> None:
    policy = load_priority_policy(POLICY_DIR)
    provider = load_provider("oslo", POLICY_DIR)
    assert (policy.source_late_after_seconds, policy.source_stale_after_seconds) == (40, 60)
    assert provider.station_report_semantics == "feed_generation"
    assert provider.service_hours != "always"
    assert (provider.service_hours.opens, provider.service_hours.closes) == ("05:00", "01:00")
    c = provider.collection
    assert (c.poll_interval_floor_seconds, c.request_deadline_seconds) == (15, 12)
    assert (c.breaker_failures, c.breaker_open_seconds, c.quarantine_probe_seconds) == (3, 60, 300)


def test_policy_dir_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "provider-oslo.yaml").write_text(
        (POLICY_DIR / "provider-oslo.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    monkeypatch.setenv("CYCLEOPS_POLICY_DIR", str(tmp_path))
    assert load_provider().system_id == "oslobysykkel"


def test_crlf_and_bom_load_the_same(tmp_path: Path) -> None:
    text = (POLICY_DIR / "priority-policy.yaml").read_text(encoding="utf-8")
    (tmp_path / "priority-policy.yaml").write_bytes(
        b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode()
    )
    assert load_priority_policy(tmp_path) == load_priority_policy(POLICY_DIR)


@pytest.mark.parametrize(
    "change",
    [
        {"sourceStaleAfterSeconds": 40},
        {"sourceStaleAfterSeconds": 30},
        {"recoveryMinFloor": 4},
        {"version": "1.0"},
        {"tickIntervalSeconds": 0},
        {"unknownKey": 1},
        {"sourceLateAfterSeconds": "40 + 5"},
    ],
)
def test_invalid_priority_policy_is_refused(change: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        PriorityPolicy.model_validate(raw("priority-policy.yaml") | change)


@pytest.mark.parametrize(
    "path_and_value",
    [
        (("collection", "requestDeadlineSeconds"), 15),
        (("collection", "requestDeadlineSeconds"), 20),
        (("collection", "pollIntervalFloorSeconds"), 0),
        (("serviceHours", "closes"), 60),  # unquoted 1:00 in YAML 1.1
        (("serviceHours", "opens"), "5:00"),
        (("serviceHours", "closes"), "24:00"),
        (("timezone",), "Europe/Nowhere"),
        (("stationReportSemantics",), "per_publication"),
    ],
)
def test_invalid_provider_is_refused(path_and_value: tuple[tuple[str, ...], Any]) -> None:
    path, value = path_and_value
    data = raw("provider-oslo.yaml")
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        ProviderConfig.model_validate(data)


def test_unquoted_time_in_yaml_is_an_integer() -> None:
    assert yaml.safe_load("closes: 1:00") == {"closes": 60}
