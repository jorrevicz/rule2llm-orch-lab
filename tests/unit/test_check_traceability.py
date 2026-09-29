import copy
import json
from pathlib import Path

import pytest

from scripts.pilot.check_traceability import check
from scripts.pilot.new_execution import open_execution

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "experiment_config.yml"
EXECUTION = "PILOT_0001"
TS = "2026-09-28T12:00:00.000Z"


def _event(seq, event_type, message_id=None, redelivered=False, service="orders"):
    return {
        "execution_id": EXECUTION,
        "task_id": "TASK_000001",
        "message_id": message_id,
        "event_seq": seq,
        "event_type": event_type,
        "service": service,
        "target": None,
        "timestamp": TS,
        "published_at": None,
        "redelivered": redelivered,
        "attempt_number": 1,
        "payload": None,
    }


def _state():
    snapshot = {
        "execution_id": EXECUTION,
        "task_id": "TASK_000001",
        "state_id": "STATE_1",
        "timestamp": TS,
        "current_event_seq": 1,
        "task": {
            "phase": "PENDING",
            "current_service": None,
            "current_target": None,
            "attempt_number": 1,
            "max_attempts": 3,
            "wait_count": 0,
            "max_waits": 2,
            "elapsed_ms": 1,
        },
        "service": {"status": "available", "latency_ms": None, "last_result": None},
        "messaging": {"queue_size": 0, "redelivered": False},
        "alternatives": {"fallback_available": False, "fallback_used": False, "alternative_targets": []},
        "recent_events": [{"event_type": "TASK_CREATED", "attempt_number": 1, "event_seq": 1}],
    }
    return {
        "execution_id": EXECUTION,
        "task_id": "TASK_000001",
        "state_id": "STATE_1",
        "timestamp": TS,
        "current_event_seq": 1,
        "system_state": snapshot,
        "recent_events": snapshot["recent_events"],
    }


def _decision():
    return {
        "execution_id": EXECUTION,
        "task_id": "TASK_000001",
        "state_id": "STATE_1",
        "decision_id": "DEC_1",
        "decision_engine": "RULES",
        "proposed_decision": {"action": "CONTINUE", "target": "inventory.primary", "reason_code": "NORMAL_FLOW"},
        "validation": {"valid": True, "error": None},
        "executed_decision": {"action": "CONTINUE", "target": "inventory.primary", "reason_code": "NORMAL_FLOW"},
        "decision_time_ms": 0.4,
        "llm_inference_ms": None,
        "token_usage": None,
        "timestamp": TS,
    }


VALID_EVENTS = [
    _event(1, "TASK_CREATED"),
    _event(2, "STOCK_RESERVATION_REQUESTED", "MSG_a"),
    _event(3, "STOCK_RESERVATION_SUCCEEDED", "MSG_b", service="inventory"),
    _event(4, "TASK_COMPLETED"),
    _event(3, "STOCK_RESERVATION_SUCCEEDED", "MSG_b", redelivered=True, service="inventory"),
]


def _write(directory: Path, name: str, records: list[dict]) -> None:
    (directory / f"{name}.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )


@pytest.fixture
def execution(tmp_path) -> Path:
    directory = open_execution(tmp_path, "normal", REPO_CONFIG)
    _write(directory, "task_events", VALID_EVENTS)
    _write(directory, "states", [_state()])
    _write(directory, "decisions", [_decision()])
    _write(directory, "microservices_logs", [{"execution_id": EXECUTION, "message": "x"}])
    return directory


def test_consistent_artifacts_pass(execution):
    result = check(execution)

    assert result.ok, result.errors
    assert result.counts == {"task_events": 5, "states": 1, "decisions": 1, "microservices_logs": 1, "fault_events": 0}


def test_gap_in_event_seq_is_reported(execution):
    events = copy.deepcopy(VALID_EVENTS)
    events[3]["event_seq"] = 5
    _write(execution, "task_events", events)

    assert any("event_seq 5 where 4" in error for error in check(execution).errors)


def test_repetition_with_a_new_event_seq_is_reported(execution):
    events = copy.deepcopy(VALID_EVENTS)
    events[4]["event_seq"] = 5
    _write(execution, "task_events", events)

    assert any("repeated MSG_b" in error for error in check(execution).errors)


def test_state_of_an_unknown_task_is_reported(execution):
    state = _state()
    state["task_id"] = state["system_state"]["task_id"] = "TASK_000009"
    _write(execution, "states", [state])

    assert any("task not found" in error for error in check(execution).errors)


def test_state_window_outside_the_trajectory_is_reported(execution):
    state = _state()
    window = [{"event_type": "INVENTORY_TIMEOUT", "attempt_number": 1, "event_seq": 2}]
    state["system_state"]["recent_events"] = state["recent_events"] = window
    _write(execution, "states", [state])

    assert any("recent_events not found" in error for error in check(execution).errors)


def test_decision_without_its_state_is_reported(execution):
    decision = _decision()
    decision["state_id"] = "STATE_404"
    _write(execution, "decisions", [decision])

    assert any("does not point to a state" in error for error in check(execution).errors)


def test_logs_from_another_execution_are_reported(execution):
    _write(execution, "microservices_logs", [{"execution_id": "PILOT_0002", "message": "x"}])

    assert any("another execution" in error for error in check(execution).errors)


def test_pilot_marked_as_eligible_is_reported(execution):
    path = execution / "execution_metadata.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    metadata["eligible_for_sample"] = True
    path.write_text(json.dumps(metadata), encoding="utf-8")

    assert any("phase/eligibility" in error for error in check(execution).errors)
