"""Protocolo: ordem das execuções, validação da integridade e registro das inválidas (M7-T06)."""

import csv

import pytest

from scripts.experiment.run_experiment import REQUIRED_ARTIFACTS, record_invalid, schedule, validate


def test_schedule_alternates_the_engine_that_opens_each_repetition():
    assert schedule(["normal", "timeout"], ["RULES", "LLM"], 2) == [
        ("normal", "RULES", 1), ("normal", "LLM", 1),
        ("normal", "LLM", 2), ("normal", "RULES", 2),
        ("timeout", "RULES", 1), ("timeout", "LLM", 1),
        ("timeout", "LLM", 2), ("timeout", "RULES", 2),
    ]


@pytest.fixture
def complete_execution(tmp_path):
    for name in REQUIRED_ARTIFACTS:
        (tmp_path / name).write_text("x", encoding="utf-8")
    return tmp_path


def summary(**overrides):
    base = {"settled": True, "traceability_ok": True, "traceability_errors": [], "duration_s": 120.0, "wall_clock_s": 120.4}
    return {**base, **overrides}


def test_complete_settled_traceable_run_is_valid(complete_execution):
    assert validate(complete_execution, summary(), http_errors=0) == []


@pytest.mark.parametrize(
    ("overrides", "http_errors", "expected"),
    [
        ({"settled": False}, 0, "did not settle"),
        ({"traceability_ok": False, "traceability_errors": ["x"]}, 0, "traceability"),
        ({}, 3, "failed at the API"),
        ({"wall_clock_s": 1320.0}, 0, "host suspended"),  # 20 min de sleep (achado M6-T10)
    ],
)
def test_bench_failures_invalidate_the_run(complete_execution, overrides, http_errors, expected):
    reasons = validate(complete_execution, summary(**overrides), http_errors=http_errors)
    assert any(expected in reason for reason in reasons)


def test_missing_artifact_invalidates_the_run(complete_execution):
    (complete_execution / "queue_metrics.csv").unlink()
    assert any("queue_metrics.csv" in reason for reason in validate(complete_execution, summary(), http_errors=0))


def test_invalid_runs_are_appended_with_their_reason(tmp_path):
    for n in (1, 2):
        record_invalid(tmp_path, {
            "execution_id": f"PILOT_000{n}", "phase": "PILOT", "scenario_id": "normal", "decision_engine": "LLM",
            "repetition_id": 1, "invalid_reason": "readiness_failed: host_power",
        })

    rows = list(csv.DictReader((tmp_path / "pilot" / "invalid_runs.csv").open()))
    assert [r["execution_id"] for r in rows] == ["PILOT_0001", "PILOT_0002"]
    assert rows[0]["invalid_reason"] == "readiness_failed: host_power"
