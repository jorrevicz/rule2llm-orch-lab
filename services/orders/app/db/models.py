"""Esquema de `orders.db` e enumerações de estado (docs/05, docs/08, docs/09).

Tabelas mínimas do piloto §23.1 mais o que o fluxo já exige: `execution_id` para
correlação, `items_json` (D-02) e a trajetória `task_events` (D-01). Novas colunas
entram quando uma task as exigir.
"""

from enum import StrEnum

from shared.task import TERMINAL_TASK_STATUSES, TaskResult, TaskStatus

__all__ = [
    "TERMINAL_TASK_STATUSES",
    "OrderStatus",
    "TaskResult",
    "TaskStatus",
    "order_status_for",
    "SCHEMA",
]


class OrderStatus(StrEnum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


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

-- Trajetória da tarefa (D-01): o StateBuilder lê a janela recent_events daqui.
-- (task_id, event_seq) NÃO é único: uma entrega repetida gera nova linha com o
-- mesmo event_seq e redelivered = 1.
CREATE TABLE IF NOT EXISTS task_events (
    event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id    TEXT NOT NULL,
    task_id         TEXT NOT NULL REFERENCES tasks (task_id),
    message_id      TEXT,
    event_seq       INTEGER NOT NULL,
    event_type      TEXT NOT NULL,
    attempt_number  INTEGER NOT NULL,
    service         TEXT NOT NULL,
    target          TEXT,
    redelivered     INTEGER NOT NULL DEFAULT 0,
    published_at    TEXT,
    recorded_at     TEXT NOT NULL,
    payload_json    TEXT
);

CREATE INDEX IF NOT EXISTS ix_tasks_status ON tasks (status);
CREATE INDEX IF NOT EXISTS ix_task_events_task ON task_events (task_id, event_seq);
CREATE INDEX IF NOT EXISTS ix_task_events_message ON task_events (message_id);
CREATE INDEX IF NOT EXISTS ix_processed_events_task ON processed_events (task_id);
"""
