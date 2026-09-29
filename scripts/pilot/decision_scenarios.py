"""Cenários de decisão de piloto contra o ambiente Docker Compose (M4).

    .venv/bin/python -m scripts.pilot.decision_scenarios [inventory_down|inventory_paused] [--observe]

Sem `--observe`, exige a sequência de decisões do Rules (teste de integração). Com
`--observe`, só registra as decisões e o desfecho: é o modo para o LLM, cujas decisões
são justamente o que se observa (a tarefa precisa apenas terminar).

- `inventory_down`: `inventory-worker` parado (sem consumidor) → espera-se
  `WAIT` até `max_waits` e `ABORT / SERVICE_UNAVAILABLE_LIMIT`.
- `inventory_paused`: `inventory-worker` congelado (`docker pause`; conectado, mas sem
  responder) → espera-se `CONTINUE`, `RETRY` até `max_attempts`, `FALLBACK` e
  `ABORT / FALLBACK_FAILED`; ao retomar, as solicitações retidas geram uma única reserva.

São perturbações de piloto, controladas e registradas no log do script; os scripts
de falha definitivos (reprodutíveis, com `fault_events.jsonl`) são do M6.
Restaura o `inventory-worker` ao final, mesmo em caso de erro.
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from scripts.pilot.environment import (
    DEFAULT_BASE_URL,
    INVENTORY_DB,
    REPO_ROOT,
    compose,
    create_order,
    scalar,
    wait_for_drained_queues,
    wait_for_terminal_status,
)
from shared.artifacts import execution_dir

DATA_ROOT = REPO_ROOT / "data"
SETTLE_SECONDS = 3.0


@dataclass
class ScenarioResult:
    scenario: str
    task_id: str | None = None
    order_status: str | None = None
    decisions: list[tuple[str, str]] = field(default_factory=list)
    reservations: int | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _execution_id() -> str:
    return compose("exec", "-T", "orders-api", "printenv", "EXECUTION_ID").strip()


def decisions_of(task_id: str) -> list[tuple[str, str]]:
    """Decisões executadas da tarefa, lidas dos arquivos por processo da execução."""
    directory = execution_dir(DATA_ROOT, _execution_id())
    records = []
    for shard in directory.glob("decisions.*.jsonl"):
        for line in shard.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record["task_id"] == task_id:
                records.append(record)
    records.sort(key=lambda record: record["timestamp"])
    return [(r["executed_decision"]["action"], r["executed_decision"]["reason_code"]) for r in records]


def _expect_terminal(result: ScenarioResult) -> None:
    if result.order_status not in {"COMPLETED", "FAILED"}:
        result.errors.append(f"task did not finish: order {result.order_status}")


def _expect(result: ScenarioResult, name: str, observed, expected) -> None:
    if observed != expected:
        result.errors.append(f"{name}: observed {observed!r}, expected {expected!r}")


def inventory_down(base_url: str = DEFAULT_BASE_URL, max_waits: int = 2, *, observe: bool = False) -> ScenarioResult:
    result = ScenarioResult("inventory_down")
    compose("stop", "inventory-worker")
    try:
        order = create_order(base_url, [{"sku": "SKU-002", "quantity": 1}])
        result.task_id = order["task_id"]
        result.order_status = wait_for_terminal_status(base_url, order["order_id"], timeout_s=30)
    finally:
        compose("start", "inventory-worker")
    time.sleep(SETTLE_SECONDS)
    result.decisions = decisions_of(result.task_id)
    if observe:
        _expect_terminal(result)
        return result
    _expect(result, "order_status", result.order_status, "FAILED")
    _expect(
        result,
        "decisions",
        result.decisions,
        [("WAIT", "SERVICE_UNAVAILABLE")] * max_waits + [("ABORT", "SERVICE_UNAVAILABLE_LIMIT")],
    )
    return result


def inventory_paused(base_url: str = DEFAULT_BASE_URL, max_attempts: int = 3, *, observe: bool = False) -> ScenarioResult:
    result = ScenarioResult("inventory_paused")
    compose("pause", "inventory-worker")
    try:
        order = create_order(base_url, [{"sku": "SKU-003", "quantity": 1}])
        result.task_id = order["task_id"]
        result.order_status = wait_for_terminal_status(base_url, order["order_id"], timeout_s=60)
    finally:
        compose("unpause", "inventory-worker")
    wait_for_drained_queues(timeout_s=30)
    time.sleep(SETTLE_SECONDS)
    result.decisions = decisions_of(result.task_id)
    result.reservations = scalar(
        INVENTORY_DB, "SELECT COUNT(*) FROM reservations WHERE task_id = ?", result.task_id
    )
    if observe:
        _expect_terminal(result)
        _expect(result, "reservations_after_resume", result.reservations, 1)
        return result
    _expect(result, "order_status", result.order_status, "FAILED")
    _expect(
        result,
        "decisions",
        result.decisions,
        [("CONTINUE", "NORMAL_FLOW")]
        + [("RETRY", "TRANSIENT_RETRY")] * (max_attempts - 1)
        + [("FALLBACK", "PRIMARY_EXHAUSTED"), ("ABORT", "FALLBACK_FAILED")],
    )
    # Achado (roadmap §13.7): sem compensação, a tarefa abortada termina com 1 reserva.
    _expect(result, "reservations_after_resume", result.reservations, 1)
    return result


SCENARIOS = {"inventory_down": inventory_down, "inventory_paused": inventory_paused}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("scenario", choices=sorted(SCENARIOS))
    parser.add_argument("--observe", action="store_true", help="só registra (modo LLM)")
    args = parser.parse_args()
    result = SCENARIOS[args.scenario](observe=args.observe)
    print(json.dumps({"ok": result.ok, **result.__dict__}, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
