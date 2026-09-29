"""Fim de um `WAIT` (docs/06 §6.3).

`orders.reevaluate` roda `wait_delay_ms` depois do `WAIT`. Se a tarefa ainda está
naquela mesma espera (status WAITING e mesmo `wait_count`), registra `WAIT_FINISHED`
e o chamador abre um novo ponto de decisão. Se a tarefa terminou no meio da espera
(ex.: resposta atrasada) ou a espera já foi encerrada, nada acontece.
"""

import sqlite3

from services.orders.app.db.connection import transaction
from services.orders.app.db.trajectory import record_internal_event
from shared.events import EventType
from shared.task import TaskStatus
from shared.timestamps import utc_now_iso


def finish_wait(connection: sqlite3.Connection, *, task_id: str, wait_count: int) -> bool:
    """True quando a espera terminou e há um novo ponto de decisão."""
    now = utc_now_iso()
    with transaction(connection):
        task = connection.execute(
            "SELECT status, wait_count FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if task is None or task["status"] != TaskStatus.WAITING or task["wait_count"] != wait_count:
            return False
        already_finished = connection.execute(
            "SELECT 1 FROM task_events WHERE task_id = ? AND event_type = ?"
            " AND json_extract(payload_json, '$.wait_count') = ? LIMIT 1",
            (task_id, EventType.WAIT_FINISHED, wait_count),
        ).fetchone()
        if already_finished:
            return False
        record_internal_event(
            connection,
            task_id=task_id,
            event_type=EventType.WAIT_FINISHED,
            now=now,
            payload={"wait_count": wait_count},
        )
    return True
