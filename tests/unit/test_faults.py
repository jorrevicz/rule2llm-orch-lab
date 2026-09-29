"""Controle e sorteio das falhas injetadas (D-20)."""

import json

import pytest
from pydantic import ValidationError

from shared.faults import (
    FAULT_CONTROL_FILE,
    FaultControl,
    FaultEventRecorder,
    clear_control,
    read_control,
    sampled,
    write_control,
)


def control(**overrides) -> FaultControl:
    fields = {
        "fault_id": "FAULT_01",
        "type": "intermittent_error",
        "target": "inventory.primary",
        "failure_probability": 0.25,
        "seed": 2048,
        "started_at": "2026-09-29T12:00:00.000Z",
    }
    return FaultControl(**{**fields, **overrides})


def test_sampling_is_reproducible_and_depends_on_every_key():
    first = [sampled(2048, f"TASK_{n:06d}", 1, probability=0.5) for n in range(200)]

    assert first == [sampled(2048, f"TASK_{n:06d}", 1, probability=0.5) for n in range(200)]
    assert first != [sampled(2049, f"TASK_{n:06d}", 1, probability=0.5) for n in range(200)]
    assert first != [sampled(2048, f"TASK_{n:06d}", 2, probability=0.5) for n in range(200)]


def test_sampling_frequency_follows_the_probability():
    hits = sum(sampled(2048, f"TASK_{n:06d}", 1, probability=0.25) for n in range(20_000))

    assert 0.23 < hits / 20_000 < 0.27
    assert not any(sampled(2048, n, probability=0.0) for n in range(1000))
    assert all(sampled(2048, n, probability=1.0) for n in range(1000))


def test_control_file_round_trip_and_absence(tmp_path):
    assert read_control(tmp_path) is None

    write_control(tmp_path, control())
    assert read_control(tmp_path) == control()
    assert not list(tmp_path.glob("*.tmp"))

    clear_control(tmp_path)
    assert not (tmp_path / FAULT_CONTROL_FILE).exists()
    clear_control(tmp_path)  # idempotente


@pytest.mark.parametrize(
    "overrides",
    [
        {"type": "timeout"},                       # sem delay_ms
        {"delay_ms": 3000},                        # delay_ms fora do timeout
        {"target": "inventory.fallback"},          # só a rota primária é degradada
        {"failure_probability": 1.5},
        {"type": "recovery"},                      # indisponibilidade não usa o arquivo
    ],
)
def test_invalid_controls_are_rejected(overrides):
    with pytest.raises(ValidationError):
        control(**overrides)


def test_recorder_writes_one_shard_per_process(tmp_path):
    recorder = FaultEventRecorder.for_process(tmp_path, "inventory-primary", "PILOT_TEST")

    recorder.record("FAULT_APPLIED", fault_id="FAULT_01", task_id="TASK_000001")

    [line] = (tmp_path / "fault_events.inventory-primary.jsonl").read_text().splitlines()
    record = json.loads(line)
    assert record["event_type"] == "FAULT_APPLIED"
    assert (record["execution_id"], record["task_id"]) == ("PILOT_TEST", "TASK_000001")
    assert record["timestamp"].endswith("Z")
