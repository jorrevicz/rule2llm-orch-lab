"""Trajetória da tarefa: tabela `task_events` de `orders.db` (docs/09, D-01, D-16).

O Orders registra aqui cada evento da tarefa, na mesma transação em que o numera:
`TASK_CREATED`, mensagens publicadas, eventos recebidos do Inventory e eventos
internos (ex.: `TASK_COMPLETED`). O `StateBuilder` lê desta tabela a janela
`recent_events`; `task_events.jsonl` é exportado dela ao fim da execução
(`scripts/pilot/collect_artifacts.py`), sem gravação dupla.

Uma entrega repetida (mesmo `message_id`) também vira linha, com o `event_seq` da
primeira ocorrência e `redelivered = 1`: por isso `(task_id, event_seq)` não é único.

Todas as funções devem ser chamadas dentro de uma transação do chamador.
"""

import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from shared.canonical_json import canonical_json
from shared.envelope import MessageEnvelope
from shared.events import EventType


class EventSource(StrEnum):
    ORDERS = "orders"
    INVENTORY = "inventory"


@dataclass(frozen=True)
class TrajectoryEvent:
    event_seq: int
    event_type: EventType
    attempt_number: int


def advance_event_seq(connection: sqlite3.Connection, *, task_id: str, now: str) -> int | None:
    """Reserva o próximo `event_seq` da tarefa. None se a tarefa não existe.

    Deve ser chamada dentro de uma transação do chamador.
    """
    updated = connection.execute(
        "UPDATE tasks SET current_event_seq = current_event_seq + 1, updated_at = ?"
        " WHERE task_id = ?",
        (now, task_id),
    )
    if updated.rowcount != 1:
        return None
    (event_seq,) = connection.execute(
        "SELECT current_event_seq FROM tasks WHERE task_id = ?", (task_id,)
    ).fetchone()
    return event_seq


def record_event(
    connection: sqlite3.Connection,
    *,
    execution_id: str,
    task_id: str,
    event_seq: int,
    event_type: EventType,
    attempt_number: int,
    service: EventSource,
    now: str,
    message_id: str | None = None,
    target: str | None = None,
    redelivered: bool = False,
    published_at: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    connection.execute(
        "INSERT INTO task_events (execution_id, task_id, message_id, event_seq, event_type,"
        " attempt_number, service, target, redelivered, published_at, recorded_at, payload_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            execution_id,
            task_id,
            message_id,
            event_seq,
            event_type,
            attempt_number,
            service,
            target,
            int(redelivered),
            published_at,
            now,
            None if payload is None else canonical_json(payload),
        ),
    )


def record_message(
    connection: sqlite3.Connection,
    message: MessageEnvelope,
    *,
    event_seq: int,
    service: EventSource,
    now: str,
    redelivered: bool = False,
) -> None:
    """Registra uma mensagem publicada ou recebida (envelope já validado)."""
    record_event(
        connection,
        execution_id=message.execution_id,
        task_id=message.task_id,
        event_seq=event_seq,
        event_type=message.event_type,
        attempt_number=message.attempt_number,
        service=service,
        now=now,
        message_id=message.message_id,
        target=message.target,
        redelivered=redelivered,
        published_at=message.published_at,
        payload=message.payload,
    )


def record_internal_event(
    connection: sqlite3.Connection, *, task_id: str, event_type: EventType, now: str
) -> int:
    """Numera e registra um evento interno do Orders; retorna o `event_seq` atribuído."""
    event_seq = advance_event_seq(connection, task_id=task_id, now=now)
    if event_seq is None:
        raise ValueError(f"unknown task {task_id}")
    task = connection.execute(
        "SELECT execution_id, attempt_number, current_target FROM tasks WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    record_event(
        connection,
        execution_id=task["execution_id"],
        task_id=task_id,
        event_seq=event_seq,
        event_type=event_type,
        attempt_number=task["attempt_number"],
        service=EventSource.ORDERS,
        target=task["current_target"],
        now=now,
    )
    return event_seq


def event_seq_of_message(connection: sqlite3.Connection, message_id: str) -> int | None:
    """`event_seq` atribuído à primeira ocorrência de uma mensagem."""
    row = connection.execute(
        "SELECT event_seq FROM task_events WHERE message_id = ? AND redelivered = 0"
        " ORDER BY event_id LIMIT 1",
        (message_id,),
    ).fetchone()
    return None if row is None else row["event_seq"]


def recent_events(connection: sqlite3.Connection, task_id: str, limit: int) -> list[TrajectoryEvent]:
    """Últimas `limit` ocorrências da trajetória, em ordem crescente (janela K)."""
    rows = connection.execute(
        "SELECT event_seq, event_type, attempt_number FROM task_events"
        " WHERE task_id = ? ORDER BY event_id DESC LIMIT ?",
        (task_id, limit),
    ).fetchall()
    return [
        TrajectoryEvent(
            event_seq=row["event_seq"],
            event_type=EventType(row["event_type"]),
            attempt_number=row["attempt_number"],
        )
        for row in reversed(rows)
    ]
