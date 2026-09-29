"""Executa o cenário de uma execução de piloto (M6-T08; metodologia Tabela 14, etapas 7–11).

    eval "$(.venv/bin/python -m scripts.pilot.new_execution --engine RULES --scenario timeout)"
    docker compose up -d --build --wait
    .venv/bin/python -m scripts.scenarios.run_scenario

1. confere que os serviços estão na mesma execução (`EXECUTION_ID`);
2. carga em malha aberta (`generate_load`) e linha do tempo da falha (`faults.injectors`)
   com o mesmo instante zero;
3. critério de encerramento: todos os pedidos enviados em estado terminal e filas vazias
   (ou o limite `--settle-timeout`, registrado como não atingido);
4. coleta (`collect_artifacts`), verificação de rastreabilidade e `scenario_summary.json`.

Reset, readiness completa, métricas de fila/containers e `run_status` são do M7. Dado de
piloto: nunca integra a amostra.
"""

import argparse
import json
import os
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from scripts.faults.injectors import injector_for, run_timeline
from scripts.pilot import check_traceability
from scripts.pilot.collect_artifacts import collect
from scripts.pilot.environment import (
    DEFAULT_BASE_URL,
    INVENTORY_DB,
    TERMINAL_ORDER_STATUSES,
    compose,
    query_sqlite_records,
    queue_consumers,
    request_json,
    wait_for_drained_queues,
    wait_until,
)
from scripts.workload.generate_load import WORKLOAD_FILE, LoadGenerator, build_plan
from shared.artifacts import Artifact, execution_dir
from shared.config import load_experiment_config
from shared.timestamps import utc_now_iso

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = REPO_ROOT / "data"
SUMMARY_FILE = "scenario_summary.json"
EXECUTION_SERVICES = ("orders-api", "orders-worker", "inventory-worker")


def services_execution_ids() -> dict[str, str]:
    return {
        service: compose("exec", "-T", service, "printenv", "EXECUTION_ID").strip()
        for service in EXECUTION_SERVICES
    }


def order_statuses(base_url: str, order_ids: list[str]) -> dict[str, str]:
    return {order_id: request_json(f"{base_url}/orders/{order_id}")["status"] for order_id in order_ids}


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def summarize(directory: Path, statuses: dict[str, str], settled: bool) -> dict:
    decisions = _read_jsonl(directory / f"{Artifact.DECISIONS}.jsonl")
    faults = _read_jsonl(directory / f"{Artifact.FAULT_EVENTS}.jsonl")
    workload = _read_jsonl(directory / WORKLOAD_FILE)
    task_ids = [record["task_id"] for record in workload if record["task_id"]]
    reservations = query_sqlite_records(
        INVENTORY_DB,
        f"SELECT task_id, route FROM reservations WHERE task_id IN ({', '.join('?' * len(task_ids))})",
        *task_ids,
    ) if task_ids else []
    inference = sorted(d["llm_inference_ms"] for d in decisions if d.get("llm_inference_ms") is not None)
    return {
        "settled": settled,
        "orders_sent": len(workload),
        "http_errors": sum(1 for record in workload if record["http_status"] != 202),
        "order_status": dict(Counter(statuses.values())),
        "reservations_by_route": dict(Counter(row["route"] for row in reservations)),
        "failed_orders_with_reservation": sum(
            1 for record in workload
            if statuses.get(record["order_id"]) == "FAILED"
            and any(row["task_id"] == record["task_id"] for row in reservations)
        ),
        "decisions": len(decisions),
        "executed_actions": dict(
            Counter(f"{d['executed_decision']['action']}/{d['executed_decision']['reason_code']}" for d in decisions)
        ),
        "invalid_decisions": dict(
            Counter(d["validation"]["error"] for d in decisions if not d["validation"]["valid"])
        ),
        "llm_inference_ms_p50": inference[len(inference) // 2] if inference else None,
        "fault_events": dict(Counter(event["event_type"] for event in faults)),
    }


@dataclass
class ScenarioRun:
    """Resultado das etapas 7–10: carga, falha e encerramento."""

    load_started_at: str
    started_wall: float
    duration_s: float
    wall_clock_s: float        # difere de duration_s se o host foi suspenso (relógio monotônico para)
    order_ids: list[str]
    statuses: dict[str, str]
    settled: bool
    queue_depths_at_end: dict[str, int]


def apply_scenario(directory: Path, execution_id: str, base_url: str, settle_timeout_s: float) -> ScenarioRun:
    """Etapas 7–10: carga em malha aberta + linha do tempo da falha + critério de encerramento."""
    config = load_experiment_config(directory / "experiment_config.yml")
    running = services_execution_ids()
    if set(running.values()) != {execution_id}:
        raise SystemExit(f"services are not on {execution_id}: {running}")

    plan = build_plan(config)
    started_at, started_wall, start = utc_now_iso(), time.time(), time.monotonic()
    generator = LoadGenerator(plan, base_url, directory, execution_id, fault=config.fault)
    injector = injector_for(
        config.fault, directory, execution_id, clock=lambda: time.monotonic() - start, consumers=queue_consumers
    )
    timeline = None
    if injector is not None:
        timeline = threading.Thread(target=run_timeline, args=(injector, config.fault, start))
        timeline.start()
    generator.start(start)
    generator.join()
    if timeline is not None:
        timeline.join()

    order_ids = [r["order_id"] for r in _read_jsonl(directory / WORKLOAD_FILE) if r["order_id"]]
    statuses: dict[str, str] = {}

    def all_terminal() -> bool:
        nonlocal statuses
        statuses = order_statuses(base_url, order_ids)
        return all(status in TERMINAL_ORDER_STATUSES for status in statuses.values())

    settled = wait_until(all_terminal, settle_timeout_s)
    depths = wait_for_drained_queues(timeout_s=30)
    return ScenarioRun(
        load_started_at=started_at,
        started_wall=started_wall,
        duration_s=round(time.monotonic() - start, 1),
        wall_clock_s=round(time.time() - started_wall, 1),
        order_ids=order_ids,
        statuses=statuses,
        settled=settled and not any(depths.values()),
        queue_depths_at_end=depths,
    )


def finish(directory: Path, execution_id: str, scenario: ScenarioRun, data_root: Path) -> dict:
    """Coleta, verificação de rastreabilidade e `scenario_summary.json`."""
    config = load_experiment_config(directory / "experiment_config.yml")
    counts = collect(execution_id, data_root)
    traceability = check_traceability.check(directory)
    summary = {
        "execution_id": execution_id,
        "scenario_id": json.loads((directory / "execution_metadata.json").read_text())["scenario_id"],
        "decision_engine": config.experiment.decision_engine,
        "duration_s": scenario.duration_s,
        "wall_clock_s": scenario.wall_clock_s,
        **summarize(directory, scenario.statuses, scenario.settled),
        "queue_depths_at_end": scenario.queue_depths_at_end,
        "artifacts": counts,
        "traceability_ok": traceability.ok,
        "traceability_errors": traceability.errors[:20],
    }
    (directory / SUMMARY_FILE).write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


def run(execution_id: str, base_url: str, data_root: Path, settle_timeout_s: float) -> dict:
    directory = execution_dir(data_root, execution_id)
    scenario = apply_scenario(directory, execution_id, base_url, settle_timeout_s)
    return finish(directory, execution_id, scenario, data_root)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--execution-id", default=os.environ.get("EXECUTION_ID"))
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--settle-timeout", type=float, default=600.0, help="segundos")
    args = parser.parse_args()
    if not args.execution_id:
        parser.error("--execution-id (ou EXECUTION_ID) é obrigatório")
    summary = run(args.execution_id, args.base_url, args.data_root, args.settle_timeout)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary["settled"] and summary["traceability_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
