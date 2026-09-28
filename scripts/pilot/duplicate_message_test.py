"""Teste de piloto de idempotência (M2): redelivery e nova tentativa não duplicam a reserva.

Pré-requisito: ambiente no ar (`docker compose up -d --build --wait`).

    .venv/bin/python -m scripts.pilot.duplicate_message_test

Roteiro:
1. `POST /orders` e espera `COMPLETED` (1 reserva).
2. Nova tentativa lógica da MESMA tarefa, como um RETRY após resposta perdida:
   novo `message_id`, mesmo `task_id`, `attempt_number` + 1. Esperado: nenhuma
   reserva nova; o Inventory responde com a reserva existente; o Orders registra
   o evento tardio sem mudar o estado da tarefa.
3. A mesma mensagem da etapa 2 é publicada mais duas vezes (mesmo `message_id`),
   como uma redelivery. Esperado: o Inventory não reprocessa e reemite a mesma
   resposta; o Orders a deduplica.

Limitação: a redelivery é simulada republicando a mesma mensagem. Para a
idempotência o efeito é idêntico, mas o flag `redelivered` do broker só aparece
numa reentrega real (ex.: worker encerrado antes do ack).

É um teste de piloto: não produz dado de amostra.
"""

import argparse
import json
import sys
from dataclasses import dataclass, field

from services.orders.app.messaging.celery_app import app as celery_app
from services.orders.app.messaging.publisher import CeleryCommandPublisher
from shared.envelope import build_envelope
from shared.events import EventType
from shared.messaging import Route
from scripts.pilot.environment import (
    DEFAULT_BASE_URL,
    INVENTORY_DB,
    ORDERS_DB,
    QUEUES,
    InspectionError,
    create_order,
    query_sqlite,
    scalar,
    wait_for_drained_queues,
    wait_for_terminal_status,
    wait_until,
)

REDELIVERIES = 2


@dataclass
class DuplicateResult:
    task_id: str | None = None
    observed: dict[str, object] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _inventory_counts(task_id: str) -> tuple[int, int]:
    reservations = scalar(INVENTORY_DB, "SELECT COUNT(*) FROM reservations WHERE task_id = ?", task_id)
    processed = scalar(INVENTORY_DB, "SELECT COUNT(*) FROM processed_messages WHERE task_id = ?", task_id)
    return reservations, processed


def _orders_state(task_id: str) -> tuple[str, int, int]:
    [[status, event_seq]] = query_sqlite(
        ORDERS_DB, "SELECT status, current_event_seq FROM tasks WHERE task_id = ?", task_id
    )
    processed = scalar(ORDERS_DB, "SELECT COUNT(*) FROM processed_events WHERE task_id = ?", task_id)
    return status, event_seq, processed


def _new_attempt(task_id: str, order_id: str, items: list[dict]) -> dict:
    [[execution_id, attempt_number]] = query_sqlite(
        ORDERS_DB, "SELECT execution_id, attempt_number FROM tasks WHERE task_id = ?", task_id
    )
    return build_envelope(
        execution_id=execution_id,
        task_id=task_id,
        event_type=EventType.STOCK_RESERVATION_REQUESTED,
        event_seq=99,  # fora da numeração do Orders: mensagem injetada pelo teste
        attempt_number=attempt_number + 1,
        target=str(Route.INVENTORY_PRIMARY),
        payload={"order_id": order_id, "items": items},
    )


def _expect(result: DuplicateResult, name: str, observed: object, expected: object) -> None:
    result.observed[name] = observed
    if observed != expected:
        result.errors.append(f"{name}: observed {observed!r}, expected {expected!r}")


def run(base_url: str = DEFAULT_BASE_URL, timeout_s: float = 30.0) -> DuplicateResult:
    result = DuplicateResult()
    publisher = CeleryCommandPublisher(celery_app)
    items = [{"sku": "SKU-DUP", "quantity": 1}]

    order = create_order(base_url, items)
    result.task_id = task_id = order["task_id"]
    status = wait_for_terminal_status(base_url, order["order_id"], timeout_s)
    if status != "COMPLETED":
        result.errors.append(f"initial order ended as {status}")
        return result

    try:
        retry = _new_attempt(task_id, order["order_id"], items)
        publisher.publish(retry, Route.INVENTORY_PRIMARY)
        wait_until(lambda: _orders_state(task_id)[2] == 2, timeout_s)
        _expect(result, "after_new_attempt.inventory(reservations, processed_messages)", _inventory_counts(task_id), (1, 2))
        _expect(result, "after_new_attempt.orders(status, event_seq, processed_events)", _orders_state(task_id), ("COMPLETED", 5, 2))

        for _ in range(REDELIVERIES):
            publisher.publish(retry, Route.INVENTORY_PRIMARY)
        depths = wait_for_drained_queues(timeout_s)
        _expect(result, "after_redelivery.inventory(reservations, processed_messages)", _inventory_counts(task_id), (1, 2))
        _expect(result, "after_redelivery.orders(status, event_seq, processed_events)", _orders_state(task_id), ("COMPLETED", 5, 2))
        _expect(result, "queues_and_dlq_empty", {name: depths.get(name, 0) for name in QUEUES}, dict.fromkeys(QUEUES, 0))
    except InspectionError as error:
        result.errors.append(f"inspection failed: {error}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    result = run(args.base_url, args.timeout)
    print(json.dumps({"ok": result.ok, **result.__dict__}, indent=2, default=list))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
