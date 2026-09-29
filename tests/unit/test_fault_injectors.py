"""Janelas de falha dos cenários e seus registros (M6-T08; RF-041)."""

import json

import pytest

from scripts.faults import injectors
from scripts.faults.injectors import (
    InventoryOutage,
    PrimaryRouteFault,
    WorkloadWindow,
    injector_for,
    run_timeline,
)
from scripts.pilot.check_traceability import CheckResult, check_fault_events
from shared.config import FaultSection
from shared.faults import FAULT_CONTROL_FILE, read_control
from tests.unit.test_config import VALID_FAULTS


def fault(fault_type: str) -> FaultSection:
    return FaultSection(type=fault_type, **VALID_FAULTS[fault_type])


def events(directory) -> list[dict]:
    path = directory / "fault_events.fault-injector.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def make(fault_type, tmp_path, **kwargs):
    return injector_for(fault(fault_type), tmp_path, "PILOT_TEST", clock=lambda: 20.0, consumers=lambda: {}, **kwargs)


@pytest.mark.parametrize(
    ("fault_type", "kind"),
    [
        ("intermittent_error", PrimaryRouteFault),
        ("timeout", PrimaryRouteFault),
        ("recovery", InventoryOutage),
        ("overload", WorkloadWindow),
        ("inconsistent_data", WorkloadWindow),
    ],
)
def test_each_fault_type_has_its_injector(fault_type, kind, tmp_path):
    assert isinstance(make(fault_type, tmp_path), kind)


def test_no_injector_without_fault(tmp_path):
    assert make("none", tmp_path) is None


@pytest.mark.parametrize("fault_type", ["intermittent_error", "timeout"])
def test_primary_route_window_writes_then_removes_the_control_file(fault_type, tmp_path):
    injector = make(fault_type, tmp_path)

    started = injector.start()
    control = read_control(tmp_path)
    assert (control.type, control.target) == (fault_type, "inventory.primary")
    assert control.failure_probability == VALID_FAULTS[fault_type]["failure_probability"]
    assert started["event_type"] == "FAULT_STARTED" and started["parameters"]["type"] == fault_type

    injector.stop()
    assert not (tmp_path / FAULT_CONTROL_FILE).exists()
    assert [e["event_type"] for e in events(tmp_path)] == ["FAULT_STARTED", "FAULT_ENDED"]


def test_outage_stops_and_restarts_the_whole_inventory_service(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(injectors, "compose", lambda *args: calls.append(args) or "")
    injector = InventoryOutage(
        fault("recovery"),
        injectors.FaultEventRecorder.for_process(tmp_path, injectors.WRITER, "PILOT_TEST"),
        lambda: 35.0,
        consumers=lambda: {"inventory.primary": 1, "inventory.fallback": 1},
    )

    injector.start()
    ended = injector.stop()

    assert calls == [("stop", "inventory-worker"), ("start", "inventory-worker")]
    assert ended["consumers_back"] is True


def test_workload_window_only_records_the_window(tmp_path):
    injector = make("overload", tmp_path)
    injector.start()
    injector.stop()

    assert [e["event_type"] for e in events(tmp_path)] == ["FAULT_STARTED", "FAULT_ENDED"]
    assert not (tmp_path / FAULT_CONTROL_FILE).exists()


class ExplodingWindow:
    def __init__(self):
        self.stopped = False

    def start(self):
        raise RuntimeError("broker gone")

    def stop(self):
        self.stopped = True


def test_timeline_always_removes_the_fault(monkeypatch):
    monkeypatch.setattr(injectors, "_sleep_until", lambda deadline: None)
    window = ExplodingWindow()

    with pytest.raises(RuntimeError):
        run_timeline(window, fault("intermittent_error"), start_monotonic=0.0)
    assert window.stopped


def test_traceability_flags_a_window_left_open():
    result = CheckResult()
    check_fault_events(
        [{"execution_id": "PILOT_TEST", "fault_id": "FAULT_TIMEOUT", "event_type": "FAULT_STARTED"}],
        "PILOT_TEST",
        result,
    )
    assert result.errors == ["fault_events: window FAULT_TIMEOUT not closed"]
