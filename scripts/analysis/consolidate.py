"""Consolidação das métricas de uma execução (M7-T07; metodologia §4.5, Tabelas 15 e 17; doc 10 §10.4).

    .venv/bin/python -m scripts.analysis.consolidate --execution-id PILOT_0050

Calculado **somente** a partir dos arquivos da execução (CLAUDE §42, §44): nada é lido
dos serviços nem do Prometheus. Arquivos gerados no diretório da execução:

- `latency_metrics.csv`: uma linha por tarefa — criação, término, estado, latência ponta a
  ponta (`TASK_CREATED` → `TASK_COMPLETED`/`TASK_ABORTED`/`MESSAGE_DEAD_LETTERED`) e nº de
  decisões;
- `throughput_metrics.csv`, `error_metrics.csv`: uma linha por execução;
- `recovery_metrics.csv` (D-24) e `blast_radius.csv` (D-25): uma linha por falha;
- `metrics_summary.csv`: uma linha com todos os indicadores escalares da execução
  (latência, throughput, erro, recuperação, blast radius, custo decisório e recursos) —
  a unidade da análise entre repetições.

Latência média/P95 são das tarefas `COMPLETED` (a taxa de erro cobre as demais). Tempo
de decisão exclui a construção do estado (D-06 / doc 10 §10.4.2). CPU em % de um núcleo;
`cpu_core_seconds` ≈ Σ CPU·Δt (área sob a curva). Campo sem dado → vazio, nunca inventado.
"""

import argparse
import csv
import json
import math
import os
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from shared.artifacts import Artifact, execution_dir
from shared.config import load_experiment_config

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = REPO_ROOT / "data"
TERMINAL_EVENTS = {"TASK_COMPLETED": "COMPLETED", "TASK_ABORTED": "ABORTED", "MESSAGE_DEAD_LETTERED": "DEAD_LETTERED"}
IDENTITY_FIELDS = ["execution_id", "phase", "scenario_id", "decision_engine", "repetition_id", "run_status"]
RECOVERY_FIELDS = [
    "fault_id", "fault_type", "fault_started_at", "fault_ended_at", "window_tasks_k", "baseline_tasks",
    "baseline_p95_ms", "recovered", "recovered_at", "recovery_time_ms", "recovery_after_fault_end_ms",
]
BLAST_RADIUS_FIELDS = [
    "fault_id", "fault_type", "tasks_total", "tasks_affected", "tasks_affected_ratio", "tasks_directly_hit",
    "tasks_interrupted", "services_with_warnings", "messages_redelivered", "messages_dead_lettered",
]


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def percentile(values: list[float], q: float) -> float | None:
    """Percentil com interpolação linear (mesmo método de `numpy.percentile` padrão)."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _round(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(value, digits)


# -- tarefas ---------------------------------------------------------------------------


def task_outcomes(events: list[dict], decisions: list[dict]) -> list[dict]:
    created, terminal = {}, {}
    for event in events:
        task, kind = event["task_id"], event["event_type"]
        if kind == "TASK_CREATED":
            created.setdefault(task, event["timestamp"])
        elif kind in TERMINAL_EVENTS and task not in terminal:
            terminal[task] = (event["timestamp"], TERMINAL_EVENTS[kind])
    decision_counts = Counter(decision["task_id"] for decision in decisions)
    rows = []
    for task, created_at in sorted(created.items(), key=lambda item: (item[1], item[0])):
        finished_at, status = terminal.get(task, (None, None))
        e2e = (parse_ts(finished_at) - parse_ts(created_at)).total_seconds() * 1000 if finished_at else None
        rows.append(
            {
                "task_id": task,
                "created_at": created_at,
                "finished_at": finished_at,
                "status": status,
                "e2e_ms": _round(e2e, 1),
                "decisions": decision_counts.get(task, 0),
            }
        )
    return rows


def latency_summary(tasks: list[dict]) -> dict:
    completed = [t["e2e_ms"] for t in tasks if t["status"] == "COMPLETED"]
    return {
        "latency_mean_ms": _round(statistics.fmean(completed), 1) if completed else None,
        "latency_median_ms": _round(statistics.median(completed), 1) if completed else None,
        "latency_p95_ms": _round(percentile(completed, 0.95), 1),
        "latency_max_ms": max(completed) if completed else None,
    }


def throughput(tasks: list[dict]) -> dict:
    finished = [t for t in tasks if t["finished_at"]]
    completed = [t for t in finished if t["status"] == "COMPLETED"]
    if not finished:
        return {"completed_tasks": 0, "window_s": None, "throughput_per_s": None}
    start = min(parse_ts(t["created_at"]) for t in tasks)
    end = max(parse_ts(t["finished_at"]) for t in finished)
    window = (end - start).total_seconds()
    return {
        "completed_tasks": len(completed),
        "window_s": _round(window, 1),
        "throughput_per_s": _round(len(completed) / window, 4) if window > 0 else None,
    }


def errors(tasks: list[dict], decisions: list[dict], workload: list[dict]) -> dict:
    statuses = Counter(t["status"] or "NOT_TERMINAL" for t in tasks)
    failed = len(tasks) - statuses.get("COMPLETED", 0)
    aborts = Counter(
        d["executed_decision"]["reason_code"] for d in decisions if d["executed_decision"]["action"] == "ABORT"
    )
    return {
        "tasks": len(tasks),
        "failed_tasks": failed,
        "error_rate": _round(failed / len(tasks), 4) if tasks else None,
        "aborted": statuses.get("ABORTED", 0),
        "dead_lettered": statuses.get("DEAD_LETTERED", 0),
        "not_terminal": statuses.get("NOT_TERMINAL", 0),
        "invalid_decisions": sum(1 for d in decisions if not d["validation"]["valid"]),
        "llm_decision_timeouts": sum(1 for d in decisions if d["validation"]["error"] == "LLM_DECISION_TIMEOUT"),
        "http_errors": sum(1 for r in workload if r["http_status"] != 202),
        "abort_reasons": json.dumps(dict(sorted(aborts.items())), ensure_ascii=False),
    }


# -- falhas: recuperação (D-24) e blast radius (D-25) -----------------------------------


def fault_windows(faults: list[dict]) -> list[dict]:
    starts = {e["fault_id"]: e for e in faults if e["event_type"] == "FAULT_STARTED"}
    ends = {e["fault_id"]: e for e in faults if e["event_type"] == "FAULT_ENDED"}
    return [
        {"fault_id": fid, "fault_type": start["fault_type"], "started_at": start["timestamp"], "ended_at": ends[fid]["timestamp"]}
        for fid, start in starts.items()
        if fid in ends
    ]


def recovery(tasks: list[dict], window: dict, k: int) -> dict:
    fault_start, fault_end = parse_ts(window["started_at"]), parse_ts(window["ended_at"])
    baseline = [t["e2e_ms"] for t in tasks if t["status"] == "COMPLETED" and parse_ts(t["created_at"]) < fault_start]
    reference = percentile(baseline, 0.95)
    row = {
        "fault_id": window["fault_id"],
        "fault_type": window["fault_type"],
        "fault_started_at": window["started_at"],
        "fault_ended_at": window["ended_at"],
        "window_tasks_k": k,
        "baseline_tasks": len(baseline),
        "baseline_p95_ms": _round(reference, 1),
        "recovered": False,
        "recovered_at": None,
        "recovery_time_ms": None,
        "recovery_after_fault_end_ms": None,
    }
    if reference is None:
        row["recovered"] = None  # sem referência pré-falha: não calculável
        return row
    after = [t for t in tasks if parse_ts(t["created_at"]) >= fault_end]
    normal = [t["status"] == "COMPLETED" and t["e2e_ms"] <= reference for t in after]
    for index in range(len(after) - k + 1):
        if all(normal[index:index + k]):
            recovered_at = parse_ts(after[index]["finished_at"])
            row.update(
                recovered=True,
                recovered_at=after[index]["finished_at"],
                recovery_time_ms=_round((recovered_at - fault_start).total_seconds() * 1000, 1),
                recovery_after_fault_end_ms=_round((recovered_at - fault_end).total_seconds() * 1000, 1),
            )
            break
    return row


def blast_radius(tasks: list[dict], faults: list[dict], window: dict, events: list[dict], logs: list[dict], queues: list[dict]) -> dict:
    affected = [t for t in tasks if t["decisions"] > 1 or t["status"] != "COMPLETED"]
    hit = {e.get("task_id") for e in faults if e["event_type"] == "FAULT_APPLIED" and e.get("task_id")}
    redelivered = 0
    for queue in {row["queue"] for row in queues}:
        values = [float(row["redelivered_total"]) for row in queues if row["queue"] == queue and row["redelivered_total"]]
        redelivered += int(max(values)) if values else 0
    return {
        "fault_id": window["fault_id"],
        "fault_type": window["fault_type"],
        "tasks_total": len(tasks),
        "tasks_affected": len(affected),
        "tasks_affected_ratio": _round(len(affected) / len(tasks), 4) if tasks else None,
        "tasks_directly_hit": len(hit),
        "tasks_interrupted": sum(1 for t in tasks if t["status"] in {"ABORTED", "DEAD_LETTERED"}),
        "services_with_warnings": json.dumps(sorted({l["service"] for l in logs if l["level"] in {"WARNING", "ERROR", "CRITICAL"}})),
        "messages_redelivered": redelivered,
        "messages_dead_lettered": sum(1 for e in events if e["event_type"] == "MESSAGE_DEAD_LETTERED"),
    }


# -- custo decisório e recursos ----------------------------------------------------------


def decision_cost(decisions: list[dict], tasks: list[dict]) -> dict:
    times = [d["decision_time_ms"] for d in decisions]
    inference = [d["llm_inference_ms"] for d in decisions if d["llm_inference_ms"] is not None]
    usage = [d["token_usage"] for d in decisions if d.get("token_usage")]
    completed = sum(1 for t in tasks if t["status"] == "COMPLETED")
    total_tokens = sum(u["total_tokens"] for u in usage) if usage else None
    return {
        "decision_count": len(decisions),
        "llm_call_count": len(inference),
        "decision_time_total_ms": _round(sum(times), 1),
        "decision_time_mean_ms": _round(statistics.fmean(times), 3) if times else None,
        "decision_time_p95_ms": _round(percentile(times, 0.95), 3),
        "llm_inference_total_ms": _round(sum(inference), 1) if inference else None,
        "llm_calls_per_task": _round(len(inference) / len(tasks), 3) if tasks and inference else None,
        "input_tokens_total": sum(u["input_tokens"] for u in usage) if usage else None,
        "output_tokens_total": sum(u["output_tokens"] for u in usage) if usage else None,
        "tokens_total": total_tokens,
        "tokens_per_completed_task": _round(total_tokens / completed, 1) if total_tokens and completed else None,
        "executed_actions": json.dumps(dict(sorted(Counter(d["executed_decision"]["action"] for d in decisions).items()))),
    }


def resource_usage(stats: list[dict]) -> dict:
    by_component: dict[str, list[dict]] = defaultdict(list)
    for row in stats:
        by_component[row["component"]].append(row)
    summary = {}
    for component, rows in sorted(by_component.items()):
        cpu = [float(r["cpu_percent"]) for r in rows if r["cpu_percent"] not in ("", None)]
        memory = [float(r["memory_bytes"]) for r in rows if r["memory_bytes"] not in ("", None)]
        times = [parse_ts(r["timestamp"]) for r in rows if r["cpu_percent"] not in ("", None)]
        core_seconds = sum(
            float(value) / 100 * (later - earlier).total_seconds()
            for value, earlier, later in zip(cpu, times, times[1:])
        )
        summary[component] = {
            "cpu_mean_percent": _round(statistics.fmean(cpu), 2) if cpu else None,
            "cpu_peak_percent": _round(max(cpu), 2) if cpu else None,
            "cpu_core_seconds": _round(core_seconds, 2) if len(cpu) > 1 else None,
            "memory_mean_bytes": int(statistics.fmean(memory)) if memory else None,
            "memory_peak_bytes": int(max(memory)) if memory else None,
        }
    return summary


def queue_summary(queues: list[dict]) -> dict:
    peaks = defaultdict(int)
    for row in queues:
        if row["messages"]:
            peaks[row["queue"]] = max(peaks[row["queue"]], int(float(row["messages"])))
    return {f"queue_peak_{queue.replace('.', '_')}": peak for queue, peak in sorted(peaks.items())}


# -- execução ------------------------------------------------------------------------------


def consolidate_execution(directory: Path) -> dict:
    metadata = json.loads((directory / "execution_metadata.json").read_text(encoding="utf-8"))
    config = load_experiment_config(directory / "experiment_config.yml")
    events = _read_jsonl(directory / f"{Artifact.TASK_EVENTS}.jsonl")
    decisions = _read_jsonl(directory / f"{Artifact.DECISIONS}.jsonl")
    faults = _read_jsonl(directory / f"{Artifact.FAULT_EVENTS}.jsonl")
    logs = _read_jsonl(directory / f"{Artifact.MICROSERVICES_LOGS}.jsonl")
    workload = _read_jsonl(directory / "workload.jsonl")
    queues = _read_csv(directory / "queue_metrics.csv")
    stats = _read_csv(directory / "container_stats.csv")

    identity = {key: metadata.get(key) for key in IDENTITY_FIELDS}
    tasks = task_outcomes(events, decisions)
    _write_csv(directory / "latency_metrics.csv", [{"execution_id": identity["execution_id"], **t} for t in tasks],
               ["execution_id", "task_id", "created_at", "finished_at", "status", "e2e_ms", "decisions"])
    rate = throughput(tasks)
    _write_csv(directory / "throughput_metrics.csv", [{**identity, **rate}], [*identity, *rate])
    error = errors(tasks, decisions, workload)
    _write_csv(directory / "error_metrics.csv", [{**identity, **error}], [*identity, *error])

    windows = fault_windows(faults)
    k = config.metrics.recovery_window_tasks
    recoveries = [{**identity, **recovery(tasks, window, k)} for window in windows]
    radii = [{**identity, **blast_radius(tasks, faults, window, events, logs, queues)} for window in windows]
    _write_csv(directory / "recovery_metrics.csv", recoveries, [*IDENTITY_FIELDS, *RECOVERY_FIELDS])
    _write_csv(directory / "blast_radius.csv", radii, [*IDENTITY_FIELDS, *BLAST_RADIUS_FIELDS])

    resources = resource_usage(stats)
    summary = {
        **identity,
        **latency_summary(tasks),
        **rate,
        **{key: value for key, value in error.items() if key != "abort_reasons"},
        **decision_cost(decisions, tasks),
        **queue_summary(queues),
        "recovery_time_ms": recoveries[0]["recovery_time_ms"] if recoveries else None,
        "recovered": recoveries[0]["recovered"] if recoveries else None,
        "tasks_affected": radii[0]["tasks_affected"] if radii else None,
        **{f"{component}_{key}": value for component, values in resources.items() for key, value in values.items()},
    }
    _write_csv(directory / "metrics_summary.csv", [summary], list(summary))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--execution-id", default=os.environ.get("EXECUTION_ID"))
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    args = parser.parse_args()
    if not args.execution_id:
        parser.error("--execution-id (ou EXECUTION_ID) é obrigatório")
    summary = consolidate_execution(execution_dir(args.data_root, args.execution_id))
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
