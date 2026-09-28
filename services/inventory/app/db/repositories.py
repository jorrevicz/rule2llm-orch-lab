"""Acesso a dados de reservas em `inventory.db`.

As verificações de idempotência (redelivery por `message_id`, nova tentativa por
`task_id`) entram em M2-T03 e M2-T04.
"""

import sqlite3

from services.inventory.app.db.connection import transaction
from services.inventory.app.db.models import ReservationRoute, ReservationStatus


def record_reservation(
    connection: sqlite3.Connection,
    *,
    message_id: str,
    task_id: str,
    order_id: str,
    route: ReservationRoute,
    items_json: str,
    now: str,
) -> None:
    """Persiste a reserva e a mensagem que a originou na mesma transação (RF-007)."""
    with transaction(connection):
        connection.execute(
            "INSERT INTO reservations (task_id, order_id, status, route, items_json, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, order_id, ReservationStatus.RESERVED, route, items_json, now),
        )
        connection.execute(
            "INSERT INTO processed_messages (message_id, task_id, processed_at) VALUES (?, ?, ?)",
            (message_id, task_id, now),
        )
