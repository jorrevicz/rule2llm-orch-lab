"""Consolidação das métricas a partir dos arquivos (M7-T07; D-24, D-25; doc 10 §10.4)."""

import csv
import json
import shutil
from datetime import UTC, datetime, timedelta

import pytest

from scripts.analysis.consolidate import consolidate_execution, percentile, recovery, task_outcomes
from tests.unit.test_config import REPO_CONFIG

T0 = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)


def ts(seconds: float) -> str:
    return (T0 + timedelta(seconds=seconds)).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def task(n: int, created: float, finished: float | None, status: str = "COMPLETED", decisions: int = 1) -> dict:
    return {
        "task_id": f"TASK_{n:06d}",
        "created_at": ts(created),
        "finished_at": None if finished is None else ts(finished),
        "status": status if finished is not None else None,
        "e2e_ms": None if finished is None else (finished - created) * 1000,
        "decisions": decisions,
    }


def test_percentile_interpolates_linearly():
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    assert percentile([10.0], 0.95) == 10.0
    assert percentile([], 0.95) is None


# -- tempo de recuperação (D-24) ---------------------------------------------------------

WINDOW = {"fault_id": "FAULT_X", "fault_type": "intermittent_error", "started_at": ts(10), "ended_at": ts(20)}


def test_recovery_is_measured_from_the_fault_start_to_the_first_normal_window():
    tasks = [task(1, 0, 1.0), task(2, 2, 3.0)]            # referência pré-falha: P95 = 1 s
    tasks += [task(3, 21, 25.0), task(4, 22, 23.0)]       # após a falha: 4 s (lento), 1 s
    tasks += [task(n, 20 + n, 20 + n + 0.5) for n in range(5, 9)]  # normais

    row = recovery(tasks, WINDOW, k=5)

    assert row["baseline_p95_ms"] == 1000.0
    assert row["recovered"] is True
    assert row["recovered_at"] == ts(23.0)                # 1º pedido da janela: TASK_000004
    assert row["recovery_time_ms"] == 13_000.0            # 23 s − início da falha (10 s)
    assert row["recovery_after_fault_end_ms"] == 3_000.0


def test_without_k_consecutive_normal_tasks_the_system_is_not_recovered():
    tasks = [task(1, 0, 1.0)] + [task(n, 20 + n, 20 + n + 0.5) for n in range(2, 5)]
    tasks.append(task(5, 26, 27.0, status="ABORTED"))

    row = recovery(tasks, WINDOW, k=5)
    assert row["recovered"] is False and row["recovery_time_ms"] is None


def test_recovery_is_not_computable_without_a_pre_fault_reference():
    row = recovery([task(1, 21, 22.0)], WINDOW, k=1)
    assert row["recovered"] is None and row["baseline_p95_ms"] is None


# -- execução completa --------------------------------------------------------------------


def _jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _decision(task_id, action, reason, *, valid=True, error=None, time_ms=1.0, inference=None, tokens=None, at=0):
    return {
        "task_id": task_id, "decision_time_ms": time_ms, "llm_inference_ms": inference, "token_usage": tokens,
        "validation": {"valid": valid, "error": error}, "timestamp": ts(at),
        "executed_decision": {"action": action, "target": None, "reason_code": reason},
    }


@pytest.fixture
def execution(tmp_path):
    shutil.copy(REPO_CONFIG, tmp_path / "experiment_config.yml")
    (tmp_path / "execution_metadata.json").write_text(json.dumps({
        "execution_id": "PILOT_TEST", "phase": "PILOT", "scenario_id": "intermittent_failure",
        "decision_engine": "LLM", "repetition_id": 1, "run_status": "VALID",
    }))
    events = []
    for n, (created, finished, terminal) in enumerate([(0, 2, "TASK_COMPLETED"), (1, 5, "TASK_COMPLETED"), (3, 9, "TASK_ABORTED")], 1):
        events += [
            {"task_id": f"TASK_{n:06d}", "event_type": "TASK_CREATED", "timestamp": ts(created)},
            {"task_id": f"TASK_{n:06d}", "event_type": terminal, "timestamp": ts(finished)},
            {"task_id": f"TASK_{n:06d}", "event_type": terminal, "timestamp": ts(finished + 1)},  # repetição
        ]
    _jsonl(tmp_path / "task_events.jsonl", events)
    usage = {"input_tokens": 500, "output_tokens": 20, "total_tokens": 520}
    _jsonl(tmp_path / "decisions.jsonl", [
        _decision("TASK_000001", "CONTINUE", "NORMAL_FLOW", time_ms=3000, inference=2990, tokens=usage),
        _decision("TASK_000002", "CONTINUE", "NORMAL_FLOW", time_ms=3000, inference=2990, tokens=usage),
        _decision("TASK_000002", "RETRY", "X", time_ms=3000, inference=2990, tokens=usage),
        _decision("TASK_000003", "ABORT", "LLM_DECISION_TIMEOUT", valid=False, error="LLM_DECISION_TIMEOUT", time_ms=30000, inference=30000),
    ])
    _jsonl(tmp_path / "fault_events.jsonl", [
        {"event_type": "FAULT_STARTED", "fault_id": "F", "fault_type": "intermittent_error", "timestamp": ts(1)},
        {"event_type": "FAULT_APPLIED", "fault_id": "F", "task_id": "TASK_000002", "timestamp": ts(2)},
        {"event_type": "FAULT_ENDED", "fault_id": "F", "fault_type": "intermittent_error", "timestamp": ts(4)},
    ])
    _jsonl(tmp_path / "microservices_logs.jsonl", [{"service": "inventory-primary", "level": "WARNING"}])
    _jsonl(tmp_path / "workload.jsonl", [{"http_status": 202}] * 3)
    with (tmp_path / "queue_metrics.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "queue", "messages", "messages_ready", "messages_unacked", "consumers",
                         "published_total", "delivered_total", "redelivered_total", "acked_total"])
        writer.writerow([ts(0), "inventory.primary", 0, 0, 0, 1, "", "", "", ""])
        writer.writerow([ts(1), "inventory.primary", 7, 7, 0, 1, 3, 3, 2, 3])
    with (tmp_path / "container_stats.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "component", "source", "cpu_percent", "memory_bytes"])
        for second, cpu in enumerate([100, 100, 100]):
            writer.writerow([ts(second), "ollama-host", "psutil", cpu, 5_000])
    return tmp_path


def _one(path):
    [row] = list(csv.DictReader(path.open()))
    return row


def test_execution_summary_is_computed_only_from_the_files(execution):
    summary = consolidate_execution(execution)

    assert (summary["latency_mean_ms"], summary["latency_p95_ms"]) == (3000.0, 3900.0)  # só COMPLETED: 2 s e 4 s
    assert (summary["completed_tasks"], summary["window_s"]) == (2, 9.0)
    assert (summary["tasks"], summary["failed_tasks"], summary["error_rate"]) == (3, 1, 0.3333)
    assert (summary["invalid_decisions"], summary["llm_decision_timeouts"]) == (1, 1)
    assert (summary["decision_count"], summary["llm_call_count"]) == (4, 4)
    assert summary["decision_time_total_ms"] == 39000.0
    assert (summary["tokens_total"], summary["tokens_per_completed_task"]) == (1560, 780.0)
    assert summary["queue_peak_inventory_primary"] == 7
    assert summary["ollama-host_cpu_core_seconds"] == 2.0          # 100% de um núcleo por 2 s
    assert summary["tasks_affected"] == 2                            # TASK_2 (2 decisões) e TASK_3 (abortada)


def test_per_file_outputs(execution):
    consolidate_execution(execution)

    latencies = list(csv.DictReader((execution / "latency_metrics.csv").open()))
    assert [(r["task_id"], r["status"], r["e2e_ms"]) for r in latencies] == [
        ("TASK_000001", "COMPLETED", "2000.0"), ("TASK_000002", "COMPLETED", "4000.0"), ("TASK_000003", "ABORTED", "6000.0")
    ]
    errors = _one(execution / "error_metrics.csv")
    assert json.loads(errors["abort_reasons"]) == {"LLM_DECISION_TIMEOUT": 1}
    radius = _one(execution / "blast_radius.csv")
    assert (radius["tasks_directly_hit"], radius["tasks_interrupted"], radius["messages_redelivered"]) == ("1", "1", "2")
    assert json.loads(radius["services_with_warnings"]) == ["inventory-primary"]
    recovery_row = _one(execution / "recovery_metrics.csv")
    assert recovery_row["fault_type"] == "intermittent_error" and recovery_row["window_tasks_k"] == "5"


def test_duplicate_terminal_events_do_not_move_the_finish_time():
    events = [
        {"task_id": "T", "event_type": "TASK_CREATED", "timestamp": ts(0)},
        {"task_id": "T", "event_type": "TASK_COMPLETED", "timestamp": ts(1)},
        {"task_id": "T", "event_type": "TASK_COMPLETED", "timestamp": ts(5)},
    ]
    [row] = task_outcomes(events, [])
    assert row["e2e_ms"] == 1000.0
