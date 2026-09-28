"""Tratamento dos eventos recebidos em `orders.events` (RF-011).

Evento terminal de sucesso conclui tarefa e pedido sem consultar o
DecisionEngine (piloto §5.2). Os demais eventos geram um ponto de decisão,
implementado a partir de M4-T04.
"""

import sqlite3

from services.orders.app.db.repositories import complete_task
from shared.envelope import MessageEnvelope
from shared.events import EventType
from shared.timestamps import utc_now_iso


class UnsupportedEventError(ValueError):
    pass


def handle_inventory_event(connection: sqlite3.Connection, event: MessageEnvelope) -> bool:
    """Aplica um evento já validado; retorna True se houve transição de estado."""
    if event.event_type == EventType.STOCK_RESERVATION_SUCCEEDED:
        return complete_task(connection, task_id=event.task_id, now=utc_now_iso())
    raise UnsupportedEventError(f"event type not handled yet: {event.event_type}")
