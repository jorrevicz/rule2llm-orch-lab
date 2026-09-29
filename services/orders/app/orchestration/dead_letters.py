"""Mensagens mortas (`tasks.dlq`) → `MESSAGE_DEAD_LETTERED` / `DEAD_LETTERED` (D-07; RF-030).

Política D-07: uma falha de processamento não prevista numa tarefa Celery rejeita a
mensagem sem requeue (sem retentativa automática escondida), e ela segue para
`tasks.dlq` — assim como a mensagem fora do contrato (D-13). O worker do Orders
consome a `tasks.dlq` e, para cada mensagem:

- identifica a tarefa (envelope em `args[0]` ou `task_id` nos kwargs das tarefas
  internas);
- registra `MESSAGE_DEAD_LETTERED` na trajetória, com a tarefa Celery, a fila de
  origem e o motivo (`x-death`), preservando o registro para análise;
- se a tarefa não é terminal, leva-a a `DEAD_LETTERED` e o pedido a `FAILED`.

Mensagem que não identifica uma tarefa conhecida é apenas registrada em log.
"""

import sqlite3
from dataclasses import dataclass
from typing import Any

from services.orders.app.db.connection import transaction
from services.orders.app.db.repositories import dead_letter_task
from services.orders.app.db.trajectory import record_internal_event
from shared.events import EventType
from shared.timestamps import utc_now_iso


@dataclass(frozen=True)
class DeadLetter:
    task_name: str | None
    task_id: str | None
    message_id: str | None
    event_type: str | None
    origin_queue: str | None
    reason: str | None

    @classmethod
    def from_celery_message(cls, body: Any, headers: dict[str, Any] | None) -> "DeadLetter":
        """Celery protocolo 2: corpo `[args, kwargs, embed]`; `x-death` do RabbitMQ."""
        headers = headers or {}
        args, kwargs = _args_and_kwargs(body)
        envelope = args[0] if args and isinstance(args[0], dict) else {}
        deaths = headers.get("x-death") or [{}]
        task_id = envelope.get("task_id") or kwargs.get("task_id")
        return cls(
            task_name=headers.get("task"),
            task_id=task_id if isinstance(task_id, str) else None,
            message_id=_text(envelope.get("message_id")),
            event_type=_text(envelope.get("event_type")),
            origin_queue=_text(deaths[0].get("queue")),
            reason=_text(deaths[0].get("reason")),
        )

    def payload(self) -> dict[str, str | None]:
        return {
            "task_name": self.task_name,
            "message_id": self.message_id,
            "event_type": self.event_type,
            "origin_queue": self.origin_queue,
            "reason": self.reason,
        }


def handle_dead_letter(connection: sqlite3.Connection, dead_letter: DeadLetter) -> bool:
    """Registra a mensagem morta; True se a tarefa passou a DEAD_LETTERED."""
    if dead_letter.task_id is None:
        return False
    now = utc_now_iso()
    with transaction(connection):
        exists = connection.execute(
            "SELECT 1 FROM tasks WHERE task_id = ?", (dead_letter.task_id,)
        ).fetchone()
        if exists is None:
            return False
        changed = dead_letter_task(connection, task_id=dead_letter.task_id, now=now)
        record_internal_event(
            connection,
            task_id=dead_letter.task_id,
            event_type=EventType.MESSAGE_DEAD_LETTERED,
            now=now,
            payload={**dead_letter.payload(), "task_state_changed": changed},
        )
    return changed


def _args_and_kwargs(body: Any) -> tuple[list, dict]:
    if isinstance(body, (list, tuple)) and len(body) >= 2:
        args, kwargs = body[0], body[1]
        return (list(args) if isinstance(args, (list, tuple)) else []), (kwargs if isinstance(kwargs, dict) else {})
    return [], {}


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None
