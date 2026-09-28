"""Detecção de timeout operacional (docs/04 §4.9; RF-023).

Timeout aqui é operacional: o Orders publicou uma solicitação e não recebeu o evento
de conclusão dentro de `inventory_timeout_ms` — não é timeout de HTTP síncrono. Após
cada despacho, o executor agenda `orders.timeout_check` com o `message_id` da
solicitação. Quando a verificação roda:

- se a tarefa já é terminal, a solicitação foi superada por outra, já teve resposta ou
  já teve o timeout registrado (ex.: redelivery da própria verificação), nada acontece;
- senão, `INVENTORY_TIMEOUT` é registrado na trajetória, `last_result` vira `timeout`
  (ou `fallback_failed`, se a solicitação foi para `inventory.fallback` — D-15) e o
  chamador abre um ponto de decisão.
"""

import sqlite3

from services.orders.app.db.connection import transaction
from services.orders.app.db.repositories import set_last_result
from services.orders.app.db.trajectory import (
    is_awaiting_reply,
    record_internal_event,
    timeout_registered,
)
from shared.events import EventType
from shared.messaging import Route
from shared.task import TERMINAL_TASK_STATUSES, TaskResult
from shared.timestamps import utc_now_iso


def register_timeout(connection: sqlite3.Connection, *, task_id: str, request_message_id: str) -> bool:
    """Registra o timeout se ele ainda se aplica; True quando há ponto de decisão."""
    now = utc_now_iso()
    with transaction(connection):
        task = connection.execute(
            "SELECT status, current_target FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if task is None or task["status"] in TERMINAL_TASK_STATUSES:
            return False
        if not is_awaiting_reply(connection, task_id, request_message_id):
            return False
        if timeout_registered(connection, task_id, request_message_id):
            return False
        result = (
            TaskResult.FALLBACK_FAILED
            if task["current_target"] == Route.INVENTORY_FALLBACK
            else TaskResult.TIMEOUT
        )
        set_last_result(connection, task_id=task_id, result=result, now=now)
        record_internal_event(
            connection,
            task_id=task_id,
            event_type=EventType.INVENTORY_TIMEOUT,
            now=now,
            payload={"request_message_id": request_message_id, "last_result": str(result)},
        )
    return True
