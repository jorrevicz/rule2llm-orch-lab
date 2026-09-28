"""Tratamento dos eventos recebidos em `orders.events` (RF-011).

Cada evento novo recebe o próximo `event_seq` da trajetória (D-16) e é aplicado na
mesma transação. Evento terminal de sucesso conclui tarefa e pedido sem consultar
o DecisionEngine (piloto §5.2). Os demais eventos geram um ponto de decisão,
implementado a partir de M4-T04.
"""

import sqlite3
from dataclasses import dataclass

from services.orders.app.db.connection import transaction
from services.orders.app.db.repositories import advance_event_seq, complete_task
from shared.envelope import MessageEnvelope
from shared.events import EventType
from shared.timestamps import utc_now_iso


class UnsupportedEventError(ValueError):
    pass


@dataclass(frozen=True)
class EventOutcome:
    recorded: bool          # o evento entrou na trajetória da tarefa
    event_seq: int | None   # posição atribuída pelo Orders (None se não registrado)
    changed_state: bool     # houve transição de estado da tarefa


NOT_RECORDED = EventOutcome(recorded=False, event_seq=None, changed_state=False)


def handle_inventory_event(connection: sqlite3.Connection, event: MessageEnvelope) -> EventOutcome:
    """Registra e aplica um evento já validado."""
    if event.event_type != EventType.STOCK_RESERVATION_SUCCEEDED:
        raise UnsupportedEventError(f"event type not handled yet: {event.event_type}")

    now = utc_now_iso()
    with transaction(connection):
        event_seq = advance_event_seq(connection, task_id=event.task_id, now=now)
        if event_seq is None:
            return NOT_RECORDED
        changed = complete_task(connection, task_id=event.task_id, now=now)
    return EventOutcome(recorded=True, event_seq=event_seq, changed_state=changed)
