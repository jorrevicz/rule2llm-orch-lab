"""Tratamento dos eventos recebidos em `orders.events` (RF-011).

Numa única transação: deduplicação por `message_id` (`processed_events`) →
numeração na trajetória (D-16) → aplicação do evento. Um evento repetido (mesmo
`message_id`, ex.: resposta reemitida pelo Inventory numa redelivery) não é
reaplicado e não consome `event_seq`.

Evento terminal de sucesso conclui tarefa e pedido sem consultar o DecisionEngine
(piloto §5.2). Os demais eventos geram um ponto de decisão, implementado a partir
de M4-T04.
"""

import sqlite3
from dataclasses import dataclass
from enum import StrEnum

from services.orders.app.db.connection import transaction
from services.orders.app.db.repositories import (
    advance_event_seq,
    complete_task,
    insert_processed_event,
    is_event_processed,
)
from shared.envelope import MessageEnvelope
from shared.events import EventType
from shared.timestamps import utc_now_iso


class UnsupportedEventError(ValueError):
    pass


class EventStatus(StrEnum):
    RECORDED = "recorded"            # evento novo, registrado na trajetória
    DUPLICATE = "duplicate"          # mesmo message_id já processado: ignorado
    UNKNOWN_TASK = "unknown_task"    # tarefa inexistente neste orders.db: ignorado


@dataclass(frozen=True)
class EventOutcome:
    status: EventStatus
    event_seq: int | None = None     # posição atribuída pelo Orders (só se RECORDED)
    changed_state: bool = False      # houve transição de estado da tarefa


def handle_inventory_event(connection: sqlite3.Connection, event: MessageEnvelope) -> EventOutcome:
    """Deduplica, registra e aplica um evento já validado."""
    if event.event_type != EventType.STOCK_RESERVATION_SUCCEEDED:
        raise UnsupportedEventError(f"event type not handled yet: {event.event_type}")

    now = utc_now_iso()
    with transaction(connection):
        if is_event_processed(connection, event.message_id):
            return EventOutcome(EventStatus.DUPLICATE)
        event_seq = advance_event_seq(connection, task_id=event.task_id, now=now)
        if event_seq is None:
            return EventOutcome(EventStatus.UNKNOWN_TASK)
        insert_processed_event(connection, event=event, now=now)
        changed = complete_task(connection, task_id=event.task_id, now=now)
    return EventOutcome(EventStatus.RECORDED, event_seq=event_seq, changed_state=changed)
