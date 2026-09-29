"""Critérios de readiness antes da janela de medição (M7-T04; RF-038, RNF-023)."""

import json

import pytest

from scripts.experiment import readiness
from scripts.experiment.readiness import (
    check_containers,
    check_databases,
    check_host,
    check_queues,
    expected_database_state,
)
from scripts.datasets.generate_dataset import CATALOG_PATH

AC = "Now drawing from 'AC Power'\n"
BATTERY = "Now drawing from 'Battery Power'\n"
SETTINGS = " sleep                0\n lowpowermode         {}\n"
ASSERTIONS = "   PreventUserIdleSystemSleep     {}\n   PreventUserIdleDisplaySleep    0\n"


def pmset(battery=AC, low_power=0, sleep_prevented=1):
    outputs = {
        ("pmset", "-g", "batt"): battery,
        ("pmset", "-g"): SETTINGS.format(low_power),
        ("pmset", "-g", "assertions"): ASSERTIONS.format(sleep_prevented),
    }
    return lambda *args: outputs[args]


def test_controlled_macos_host_passes():
    assert check_host("Darwin", pmset()).ok


@pytest.mark.parametrize(
    ("kwargs", "failing"),
    [
        ({"battery": BATTERY}, "ac_power"),
        ({"low_power": 1}, "low_power_mode_off"),
        ({"sleep_prevented": 0}, "sleep_prevented"),
    ],
)
def test_uncontrolled_macos_host_fails(kwargs, failing):
    result = check_host("Darwin", pmset(**kwargs))

    assert not result.ok
    assert result.detail[failing] is False


def test_power_checks_do_not_apply_on_the_linux_server():
    def forbidden(*_):
        raise AssertionError("pmset must not run on Linux")

    result = check_host("Linux", forbidden)
    assert result.ok and result.detail["power_checks"] == "not_applicable"


def _state(messages=0, consumers=1):
    queues = ("inventory.primary", "inventory.fallback", "orders.events", "tasks.dlq")
    return {"queues": {"messages": {q: messages for q in queues}, "consumers": {q: consumers for q in queues}}}


def test_queues_must_be_declared_empty_and_consumed():
    assert check_queues(_state()).ok
    assert not check_queues(_state(messages=1)).ok
    assert not check_queues(_state(consumers=0)).ok
    missing = _state()
    del missing["queues"]["messages"]["tasks.dlq"]
    assert not check_queues(missing).ok


def test_databases_must_match_freshly_created_ones():
    expected = expected_database_state(CATALOG_PATH)

    assert check_databases({"databases": expected}, expected).ok
    dirty = json.loads(json.dumps(expected))
    dirty["orders"]["logical_sha256"] = "0" * 64
    assert not check_databases({"databases": dirty}, expected).ok


def test_every_required_container_must_be_running_and_healthy(monkeypatch):
    rows = [{"Service": name, "State": "running", "Health": ""} for name in readiness.REQUIRED_SERVICES]
    monkeypatch.setattr(readiness, "compose", lambda *args: "\n".join(json.dumps(row) for row in rows))
    assert check_containers().ok

    rows[0]["Health"] = "starting"
    assert not check_containers().ok
    rows[0]["Health"] = ""
    rows.pop()
    assert not check_containers().ok
