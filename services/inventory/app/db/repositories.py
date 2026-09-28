"""Acesso a dados de reservas em `inventory.db`.

As funções devem ser chamadas dentro de uma transação do chamador, para que a
verificação de idempotência e a gravação aconteçam atomicamente.
"""

import sqlite3
from dataclasses import dataclass

from services.inventory.app.db.models import (
    ProcessingResult,
    ReservationRoute,
    ReservationStatus,
)


@dataclass(frozen=True)
class ProcessedMessage:
    message_id: str
    task_id: str
    result: ProcessingResult
    response_json: str


def find_processed_message(
    connection: sqlite3.Connection, message_id: str
) -> ProcessedMessage | None:
    row = connection.execute(
        "SELECT message_id, task_id, result, response_json FROM processed_messages"
        " WHERE message_id = ?",
        (message_id,),
    ).fetchone()
    if row is None:
        return None
    return ProcessedMessage(
        message_id=row["message_id"],
        task_id=row["task_id"],
        result=ProcessingResult(row["result"]),
        response_json=row["response_json"],
    )


def insert_reservation(
    connection: sqlite3.Connection,
    *,
    task_id: str,
    order_id: str,
    route: ReservationRoute,
    items_json: str,
    now: str,
) -> None:
    connection.execute(
        "INSERT INTO reservations (task_id, order_id, status, route, items_json, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (task_id, order_id, ReservationStatus.RESERVED, route, items_json, now),
    )


def insert_processed_message(
    connection: sqlite3.Connection,
    *,
    message_id: str,
    task_id: str,
    event_type: str,
    result: ProcessingResult,
    response_json: str,
    now: str,
) -> None:
    connection.execute(
        "INSERT INTO processed_messages"
        " (message_id, task_id, event_type, result, response_json, processed_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (message_id, task_id, event_type, result, response_json, now),
    )
