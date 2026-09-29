"""Acesso a dados de pedidos e tarefas em `orders.db`.

Funções com `with transaction(...)` próprio são casos de uso de um passo. As que
não abrem transação devem ser chamadas DENTRO de uma transação do chamador, para
compor passos que precisam ser atômicos (ex.: numerar e aplicar um evento).

Numeração da trajetória (`event_seq`, D-16): o Orders numera todos os eventos da
tarefa, de forma monotônica. `TASK_CREATED` = 1; cada mensagem publicada, cada
evento novo recebido e cada evento interno registrado consomem o próximo número.
O registro de cada evento fica em `services/orders/app/db/trajectory.py`.
"""

import sqlite3
from dataclasses import dataclass

from services.orders.app.db.connection import transaction
from services.orders.app.db.models import (
    TERMINAL_TASK_STATUSES,
    OrderStatus,
    TaskResult,
    TaskStatus,
    order_status_for,
)
from services.orders.app.db.trajectory import EventSource, record_event
from shared.envelope import MessageEnvelope
from shared.events import EventType
from shared.ids import IdPrefix, sequential_id

TASK_CREATED_EVENT_SEQ = 1


@dataclass(frozen=True)
class CreatedOrder:
    order_id: str
    task_id: str
    status: OrderStatus


@dataclass(frozen=True)
class OrderView:
    order_id: str
    task_id: str
    status: OrderStatus


@dataclass(frozen=True)
class DispatchContext:
    """O que é preciso para montar a solicitação de reserva da tentativa corrente."""

    task_id: str
    order_id: str
    execution_id: str
    target: str
    attempt_number: int
    items_json: str


def create_order_with_task(
    connection: sqlite3.Connection, *, execution_id: str, items_json: str, now: str
) -> CreatedOrder:
    """Persiste pedido, tarefa e `TASK_CREATED` na mesma transação (RF-003, RF-004).

    Pedido e tarefa compartilham o número sequencial (`ORD_000001` ↔ `TASK_000001`),
    pois a relação é 1:1 no recorte atual.
    """

    with transaction(connection):
        number = _next_order_number(connection)
        order_id = sequential_id(IdPrefix.ORDER, number)
        task_id = sequential_id(IdPrefix.TASK, number)
        connection.execute(
            "INSERT INTO orders (order_id, execution_id, status, items_json, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (order_id, execution_id, OrderStatus.PENDING, items_json, now, now),
        )
        connection.execute(
            "INSERT INTO tasks (task_id, order_id, execution_id, status, current_event_seq,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (task_id, order_id, execution_id, TaskStatus.PENDING, TASK_CREATED_EVENT_SEQ, now, now),
        )
        record_event(
            connection,
            execution_id=execution_id,
            task_id=task_id,
            event_seq=TASK_CREATED_EVENT_SEQ,
            event_type=EventType.TASK_CREATED,
            attempt_number=1,
            service=EventSource.ORDERS,
            now=now,
        )
    return CreatedOrder(order_id=order_id, task_id=task_id, status=OrderStatus.PENDING)


def get_order(connection: sqlite3.Connection, order_id: str) -> OrderView | None:
    row = connection.execute(
        "SELECT o.order_id, t.task_id, o.status FROM orders o"
        " JOIN tasks t ON t.order_id = o.order_id WHERE o.order_id = ?",
        (order_id,),
    ).fetchone()
    if row is None:
        return None
    return OrderView(order_id=row["order_id"], task_id=row["task_id"], status=OrderStatus(row["status"]))


def start_first_dispatch(connection: sqlite3.Connection, *, task_id: str, target: str, now: str) -> bool:
    """`CONTINUE`: PENDING/WAITING → DISPATCHED, antes de qualquer despacho.

    Deve ser chamada dentro de uma transação do chamador.
    """
    updated = connection.execute(
        "UPDATE tasks SET status = ?, current_target = ?, updated_at = ?"
        " WHERE task_id = ? AND status IN (?, ?) AND current_target IS NULL",
        (TaskStatus.DISPATCHED, target, now, task_id, TaskStatus.PENDING, TaskStatus.WAITING),
    )
    return updated.rowcount == 1


def start_retry(connection: sqlite3.Connection, *, task_id: str, now: str) -> int | None:
    """`RETRY`: nova tentativa lógica (`attempt_number + 1`), tarefa → RETRYING.

    Só para tarefa já despachada e não terminal. Retorna o novo `attempt_number`.
    Deve ser chamada dentro de uma transação do chamador.
    """
    terminal = tuple(TERMINAL_TASK_STATUSES)
    updated = connection.execute(
        "UPDATE tasks SET attempt_number = attempt_number + 1, status = ?, updated_at = ?"
        f" WHERE task_id = ? AND current_target IS NOT NULL AND status NOT IN ({', '.join('?' * len(terminal))})",
        (TaskStatus.RETRYING, now, task_id, *terminal),
    )
    if updated.rowcount != 1:
        return None
    (attempt_number,) = connection.execute(
        "SELECT attempt_number FROM tasks WHERE task_id = ?", (task_id,)
    ).fetchone()
    return attempt_number


def mark_retry_dispatched(connection: sqlite3.Connection, *, task_id: str, now: str) -> bool:
    """RETRYING → DISPATCHED quando o despacho agendado da nova tentativa acontece."""
    updated = connection.execute(
        "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ? AND status = ?",
        (TaskStatus.DISPATCHED, now, task_id, TaskStatus.RETRYING),
    )
    return updated.rowcount == 1


def start_fallback(connection: sqlite3.Connection, *, task_id: str, target: str, now: str) -> bool:
    """`FALLBACK`: troca o target, marca `fallback_used`; `attempt_number` não muda (D-15).

    Só se o fallback ainda não foi usado e a tarefa não é terminal. Deve ser chamada
    dentro de uma transação do chamador.
    """
    terminal = tuple(TERMINAL_TASK_STATUSES)
    updated = connection.execute(
        "UPDATE tasks SET status = ?, current_target = ?, fallback_used = 1, updated_at = ?"
        f" WHERE task_id = ? AND fallback_used = 0 AND status NOT IN ({', '.join('?' * len(terminal))})",
        (TaskStatus.FALLBACK_PROCESSING, target, now, task_id, *terminal),
    )
    return updated.rowcount == 1


def enter_wait(connection: sqlite3.Connection, *, task_id: str, now: str) -> int | None:
    """`WAIT`: tarefa → WAITING e `wait_count + 1`; `attempt_number` não muda.

    Retorna o novo `wait_count` (None se a tarefa é terminal). Deve ser chamada dentro
    de uma transação do chamador.
    """
    terminal = tuple(TERMINAL_TASK_STATUSES)
    updated = connection.execute(
        "UPDATE tasks SET wait_count = wait_count + 1, status = ?, updated_at = ?"
        f" WHERE task_id = ? AND status NOT IN ({', '.join('?' * len(terminal))})",
        (TaskStatus.WAITING, now, task_id, *terminal),
    )
    if updated.rowcount != 1:
        return None
    (wait_count,) = connection.execute(
        "SELECT wait_count FROM tasks WHERE task_id = ?", (task_id,)
    ).fetchone()
    return wait_count


def dispatch_context(connection: sqlite3.Connection, task_id: str) -> DispatchContext:
    row = connection.execute(
        "SELECT t.task_id, t.order_id, t.execution_id, t.current_target, t.attempt_number,"
        " o.items_json FROM tasks t JOIN orders o ON o.order_id = t.order_id WHERE t.task_id = ?",
        (task_id,),
    ).fetchone()
    return DispatchContext(
        task_id=row["task_id"],
        order_id=row["order_id"],
        execution_id=row["execution_id"],
        target=row["current_target"],
        attempt_number=row["attempt_number"],
        items_json=row["items_json"],
    )


def is_event_processed(connection: sqlite3.Connection, message_id: str) -> bool:
    """Idempotência de transporte no consumo de `orders.events` (docs/04 §4.8)."""
    row = connection.execute(
        "SELECT 1 FROM processed_events WHERE message_id = ?", (message_id,)
    ).fetchone()
    return row is not None


def insert_processed_event(
    connection: sqlite3.Connection, *, event: MessageEnvelope, now: str
) -> None:
    """Deve ser chamada dentro de uma transação do chamador."""
    connection.execute(
        "INSERT INTO processed_events (message_id, task_id, event_type, processed_at)"
        " VALUES (?, ?, ?, ?)",
        (event.message_id, event.task_id, event.event_type, now),
    )


def complete_task(connection: sqlite3.Connection, *, task_id: str, now: str) -> bool:
    """Conclui tarefa e pedido (docs/05 §5.4). Retorna False se não houve transição.

    Tarefa já terminal não é alterada. Deve ser chamada dentro de uma transação do
    chamador.
    """
    terminal = tuple(TERMINAL_TASK_STATUSES)
    updated = connection.execute(
        "UPDATE tasks SET status = ?, last_result = ?, updated_at = ?"
        f" WHERE task_id = ? AND status NOT IN ({', '.join('?' * len(terminal))})",
        (TaskStatus.COMPLETED, TaskResult.OK, now, task_id, *terminal),
    )
    if updated.rowcount != 1:
        return False
    connection.execute(
        "UPDATE orders SET status = ?, updated_at = ?"
        " WHERE order_id = (SELECT order_id FROM tasks WHERE task_id = ?)",
        (order_status_for(TaskStatus.COMPLETED), now, task_id),
    )
    return True


def set_last_result(connection: sqlite3.Connection, *, task_id: str, result: TaskResult, now: str) -> None:
    """Resultado da última etapa, lido pelo `StateBuilder` (`service.last_result`)."""
    connection.execute(
        "UPDATE tasks SET last_result = ?, updated_at = ? WHERE task_id = ?", (result, now, task_id)
    )


def abort_task(connection: sqlite3.Connection, *, task_id: str, now: str) -> bool:
    """Task → ABORTED e Order → FAILED (docs/05 §5.4). False se já era terminal.

    Deve ser chamada dentro de uma transação do chamador.
    """
    return _finish_task(connection, task_id=task_id, status=TaskStatus.ABORTED, now=now)


def _finish_task(
    connection: sqlite3.Connection, *, task_id: str, status: TaskStatus, now: str
) -> bool:
    terminal = tuple(TERMINAL_TASK_STATUSES)
    updated = connection.execute(
        "UPDATE tasks SET status = ?, updated_at = ?"
        f" WHERE task_id = ? AND status NOT IN ({', '.join('?' * len(terminal))})",
        (status, now, task_id, *terminal),
    )
    if updated.rowcount != 1:
        return False
    connection.execute(
        "UPDATE orders SET status = ?, updated_at = ?"
        " WHERE order_id = (SELECT order_id FROM tasks WHERE task_id = ?)",
        (order_status_for(status), now, task_id),
    )
    return True


def _next_order_number(connection: sqlite3.Connection) -> int:
    # Seguro sob BEGIN IMMEDIATE: nenhum outro escritor lê o mesmo máximo.
    (current,) = connection.execute("SELECT COALESCE(MAX(rowid), 0) FROM orders").fetchone()
    return current + 1
