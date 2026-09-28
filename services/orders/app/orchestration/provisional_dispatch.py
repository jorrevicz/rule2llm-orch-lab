"""Despacho inicial PROVISÓRIO da tarefa (M1-T05).

Equivale a um `CONTINUE` fixo para `inventory.primary`, SEM StateBuilder, sem
motor de decisão, sem validação e sem registro de decisão. Existe apenas para o
fluxo normal do M1 funcionar ponta a ponta e é substituído pelo Orchestrator
(StateBuilder → DecisionEngine → Validator → Executor) em M4-T04.
"""

import json
import sqlite3

from services.orders.app.db.repositories import mark_task_dispatched
from services.orders.app.messaging.publisher import CommandPublisher
from shared.envelope import Envelope, build_envelope
from shared.events import EventType
from shared.messaging import Route
from shared.timestamps import utc_now_iso


def dispatch_initial_reservation(
    connection: sqlite3.Connection, task_id: str, publisher: CommandPublisher
) -> Envelope:
    route = Route.INVENTORY_PRIMARY
    task = mark_task_dispatched(connection, task_id=task_id, target=str(route), now=utc_now_iso())
    envelope = build_envelope(
        execution_id=task.execution_id,
        task_id=task.task_id,
        event_type=EventType.STOCK_RESERVATION_REQUESTED,
        event_seq=task.event_seq,
        attempt_number=task.attempt_number,
        target=task.target,
        payload={"order_id": task.order_id, "items": json.loads(task.items_json)},
    )
    publisher.publish(envelope, route)
    return envelope
