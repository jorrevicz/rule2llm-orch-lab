"""Despacho inicial PROVISÓRIO da tarefa (M1-T05).

Equivale a um `CONTINUE` fixo para `inventory.primary`, SEM motor de decisão, sem
validação e sem registro de decisão. Existe apenas para o fluxo normal funcionar
ponta a ponta e é substituído pelo Orchestrator (StateBuilder → DecisionEngine →
Validator → Executor) em M4-T04.

Ponto de decisão "início da tarefa": o `SYSTEM_STATE` é construído e gravado em
`states.jsonl` (M3-T05), mas nenhum motor o avalia ainda. Depois, numa transação:
PENDING → DISPATCHED, envelope montado e `STOCK_RESERVATION_REQUESTED` registrado na
trajetória. A publicação acontece depois do commit.
"""

import json
import sqlite3

from services.orders.app.db.connection import transaction
from services.orders.app.db.repositories import mark_task_dispatched
from services.orders.app.db.trajectory import EventSource, record_message
from services.orders.app.messaging.publisher import CommandPublisher
from services.orders.app.orchestration.coordination import Coordination
from shared.envelope import Envelope, MessageEnvelope, build_envelope
from shared.events import EventType
from shared.messaging import Route
from shared.timestamps import utc_now_iso


def start_task(connection: sqlite3.Connection, task_id: str, coordination: Coordination) -> Envelope:
    state = coordination.state_builder.build(connection, task_id)
    coordination.state_recorder.record(state)
    return dispatch_initial_reservation(connection, task_id, coordination.publisher)


def dispatch_initial_reservation(
    connection: sqlite3.Connection, task_id: str, publisher: CommandPublisher
) -> Envelope:
    route = Route.INVENTORY_PRIMARY
    now = utc_now_iso()
    with transaction(connection):
        task = mark_task_dispatched(connection, task_id=task_id, target=str(route), now=now)
        envelope = build_envelope(
            execution_id=task.execution_id,
            task_id=task.task_id,
            event_type=EventType.STOCK_RESERVATION_REQUESTED,
            event_seq=task.event_seq,
            attempt_number=task.attempt_number,
            target=task.target,
            payload={"order_id": task.order_id, "items": json.loads(task.items_json)},
        )
        record_message(
            connection,
            MessageEnvelope.model_validate(envelope),
            event_seq=task.event_seq,
            service=EventSource.ORDERS,
            now=now,
        )
    publisher.publish(envelope, route)
    return envelope
