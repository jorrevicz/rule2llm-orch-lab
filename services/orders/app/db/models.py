"""Esquema de `orders.db` e enumerações de estado (docs/05, docs/08, docs/09).

Tabelas mínimas do piloto §23.1 mais o que o fluxo já exige: `execution_id` para
correlação e `items_json` (D-02). Novas colunas entram quando uma task as exigir.
"""

from enum import StrEnum


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    DISPATCHED = "DISPATCHED"
    PROCESSING = "PROCESSING"
    WAITING = "WAITING"
    RETRYING = "RETRYING"
    FALLBACK_PROCESSING = "FALLBACK_PROCESSING"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"
    DEAD_LETTERED = "DEAD_LETTERED"


class TaskResult(StrEnum):
    """Valores de `tasks.last_result` (docs/06 §6.2.1, `service.last_result`)."""

    OK = "ok"
    TIMEOUT = "timeout"
    TRANSIENT_ERROR = "transient_error"
    INVALID_DATA = "invalid_data"
    FALLBACK_FAILED = "fallback_failed"


TERMINAL_TASK_STATUSES = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.ABORTED, TaskStatus.DEAD_LETTERED}
)


def order_status_for(task_status: TaskStatus) -> OrderStatus:
    """Mapeamento tarefa → pedido (docs/05 §5.4)."""
    if task_status == TaskStatus.COMPLETED:
        return OrderStatus.COMPLETED
    if task_status in (TaskStatus.ABORTED, TaskStatus.DEAD_LETTERED):
        return OrderStatus.FAILED
    return OrderStatus.PENDING


SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    order_id      TEXT PRIMARY KEY,
    execution_id  TEXT NOT NULL,
    status        TEXT NOT NULL,
    items_json    TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id            TEXT PRIMARY KEY,
    order_id           TEXT NOT NULL UNIQUE REFERENCES orders (order_id),
    execution_id       TEXT NOT NULL,
    status             TEXT NOT NULL,
    current_target     TEXT,
    attempt_number     INTEGER NOT NULL DEFAULT 1,
    wait_count         INTEGER NOT NULL DEFAULT 0,
    fallback_used      INTEGER NOT NULL DEFAULT 0,
    current_event_seq  INTEGER NOT NULL DEFAULT 0,
    last_result        TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processed_events (
    message_id    TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL,
    event_type    TEXT NOT NULL,
    processed_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_tasks_status ON tasks (status);
CREATE INDEX IF NOT EXISTS ix_processed_events_task ON processed_events (task_id);
"""
