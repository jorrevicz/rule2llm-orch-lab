"""Smoke test do fluxo normal (M1): POST /orders → Inventory → COMPLETED.

Pré-requisito: ambiente no ar (`docker compose up -d --build --wait`).

    python scripts/pilot/smoke_test.py [--orders 3] [--timeout 30]

Sai com código 0 somente se:
- todos os pedidos terminam `COMPLETED`;
- cada tarefa tem exatamente uma reserva em `inventory.db`;
- as filas de negócio e a `tasks.dlq` terminam vazias.

É um teste de piloto: não produz dado de amostra.
"""

import argparse
import json
import sys
from dataclasses import dataclass, field

from scripts.pilot.environment import (
    DEFAULT_BASE_URL,
    INVENTORY_DB,
    QUEUES,
    InspectionError,
    create_order,
    scalar,
    wait_for_drained_queues,
    wait_for_terminal_status,
)

QUEUE_DRAIN_TIMEOUT_SECONDS = 10.0


@dataclass
class SmokeResult:
    orders: dict[str, str] = field(default_factory=dict)  # order_id -> status final
    reservations: dict[str, int] = field(default_factory=dict)  # task_id -> nº de reservas
    queue_depths: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def reservation_count(task_id: str) -> int:
    return scalar(INVENTORY_DB, "SELECT COUNT(*) FROM reservations WHERE task_id = ?", task_id)


def run(base_url: str = DEFAULT_BASE_URL, orders: int = 1, timeout_s: float = 30.0) -> SmokeResult:
    result = SmokeResult()
    created = [
        create_order(base_url, [{"sku": f"SKU-{index:03d}", "quantity": index}])
        for index in range(1, orders + 1)
    ]
    for order in created:
        status = wait_for_terminal_status(base_url, order["order_id"], timeout_s)
        result.orders[order["order_id"]] = status
        if status != "COMPLETED":
            result.errors.append(f"{order['order_id']} ended as {status}")

    try:
        result.reservations = {order["task_id"]: reservation_count(order["task_id"]) for order in created}
        result.queue_depths = wait_for_drained_queues(QUEUE_DRAIN_TIMEOUT_SECONDS)
    except InspectionError as error:
        result.errors.append(f"inspection failed: {error}")
        return result

    for task_id, count in result.reservations.items():
        if count != 1:
            result.errors.append(f"{task_id} has {count} reservations (expected 1)")
    for name in QUEUES:
        if result.queue_depths.get(name, 0):
            result.errors.append(f"queue {name} not empty: {result.queue_depths[name]}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--orders", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=30.0, help="segundos por pedido")
    args = parser.parse_args()

    result = run(args.base_url, args.orders, args.timeout)
    print(json.dumps({"ok": result.ok, **result.__dict__}, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
