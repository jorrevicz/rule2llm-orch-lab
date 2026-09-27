"""Acesso a dados de pedidos e tarefas em `orders.db`."""

import sqlite3
from dataclasses import dataclass

from services.orders.app.db.connection import transaction
from services.orders.app.db.models import OrderStatus, TaskStatus
from shared.ids import IdPrefix, sequential_id


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


def create_order_with_task(
    connection: sqlite3.Connection, *, execution_id: str, items_json: str, now: str
) -> CreatedOrder:
    """Persiste pedido e tarefa na mesma transação (RF-003, RF-004).

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
            "INSERT INTO tasks (task_id, order_id, execution_id, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, order_id, execution_id, TaskStatus.PENDING, now, now),
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


def _next_order_number(connection: sqlite3.Connection) -> int:
    # Seguro sob BEGIN IMMEDIATE: nenhum outro escritor lê o mesmo máximo.
    (current,) = connection.execute("SELECT COALESCE(MAX(rowid), 0) FROM orders").fetchone()
    return current + 1
