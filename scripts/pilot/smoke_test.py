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
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
DEFAULT_BASE_URL = "http://localhost:8000"
QUEUES = ("inventory.primary", "inventory.fallback", "orders.events", "tasks.dlq")
TERMINAL_ORDER_STATUSES = {"COMPLETED", "FAILED"}
POLL_INTERVAL_SECONDS = 0.2
QUEUE_DRAIN_TIMEOUT_SECONDS = 10.0

_RESERVATION_COUNT_SCRIPT = """
import json, sqlite3, sys
connection = sqlite3.connect("/data/inventory.db")
counts = {
    task_id: connection.execute(
        "SELECT COUNT(*) FROM reservations WHERE task_id = ?", (task_id,)
    ).fetchone()[0]
    for task_id in sys.argv[1:]
}
print(json.dumps(counts))
"""


@dataclass
class SmokeResult:
    orders: dict[str, str] = field(default_factory=dict)  # order_id -> status final
    reservations: dict[str, int] = field(default_factory=dict)  # task_id -> nº de reservas
    queue_depths: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _request_json(url: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        url, data=data, headers={"content-type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def create_order(base_url: str, items: list[dict]) -> dict:
    return _request_json(f"{base_url}/orders", {"items": items})


def wait_for_terminal_status(base_url: str, order_id: str, timeout_s: float) -> str:
    deadline = time.monotonic() + timeout_s
    status = "UNKNOWN"
    while time.monotonic() < deadline:
        status = _request_json(f"{base_url}/orders/{order_id}")["status"]
        if status in TERMINAL_ORDER_STATUSES:
            return status
        time.sleep(POLL_INTERVAL_SECONDS)
    return status


class InspectionError(RuntimeError):
    pass


def _compose(*args: str) -> str:
    command = ["docker", "compose", "-f", str(COMPOSE_FILE), *args]
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        raise InspectionError(f"{' '.join(args[:3])}: {completed.stderr.strip()}")
    return completed.stdout


def reservation_counts(task_ids: list[str]) -> dict[str, int]:
    # `run --no-deps` usa um container efêmero com o mesmo volume: funciona mesmo
    # com o inventory-worker parado.
    output = _compose(
        "run", "--rm", "--no-deps", "-T", "inventory-worker",
        "python", "-c", _RESERVATION_COUNT_SCRIPT, *task_ids,
    )
    return json.loads(output)


def queue_depths() -> dict[str, int]:
    output = _compose(
        "exec", "-T", "rabbitmq",
        "rabbitmqctl", "list_queues", "name", "messages", "--formatter", "json", "-q",
    )
    return {queue["name"]: queue["messages"] for queue in json.loads(output)}


def wait_for_drained_queues(timeout_s: float) -> dict[str, int]:
    # O ack (acks_late) acontece logo após o processamento; pode haver alguns
    # milissegundos entre o pedido aparecer COMPLETED e a fila zerar.
    deadline = time.monotonic() + timeout_s
    depths = queue_depths()
    while any(depths.get(name, 0) for name in QUEUES) and time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL_SECONDS)
        depths = queue_depths()
    return depths


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
        result.reservations = reservation_counts([order["task_id"] for order in created])
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
