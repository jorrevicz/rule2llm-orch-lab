"""Endpoints de pedidos: `POST /orders` e `GET /orders/{order_id}`."""

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from services.orders.app.api.schemas import CreateOrderRequest, OrderResponse
from services.orders.app.db.connection import connect
from services.orders.app.db.repositories import create_order_with_task, get_order
from services.orders.app.orchestration.coordination import Coordination
from services.orders.app.orchestration.provisional_dispatch import start_task
from services.orders.app.settings import Settings
from shared.canonical_json import canonical_json
from shared.timestamps import utc_now_iso

router = APIRouter()


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_coordination(request: Request) -> Coordination:
    return request.app.state.coordination


def get_connection(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Iterator[sqlite3.Connection]:
    connection = connect(settings.database_path)
    try:
        yield connection
    finally:
        connection.close()


Connection = Annotated[sqlite3.Connection, Depends(get_connection)]


@router.post("/orders", status_code=status.HTTP_202_ACCEPTED, response_model=OrderResponse)
def create_order(
    body: CreateOrderRequest,
    connection: Connection,
    settings: Annotated[Settings, Depends(get_settings)],
    coordination: Annotated[Coordination, Depends(get_coordination)],
) -> OrderResponse:
    items_json = canonical_json([item.model_dump() for item in body.items])
    created = create_order_with_task(
        connection, execution_id=settings.execution_id, items_json=items_json, now=utc_now_iso()
    )
    start_task(connection, created.task_id, coordination)
    return OrderResponse(order_id=created.order_id, task_id=created.task_id, status=created.status)


@router.get("/orders/{order_id}", response_model=OrderResponse)
def read_order(order_id: str, connection: Connection) -> OrderResponse:
    order = get_order(connection, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="order not found")
    return OrderResponse(order_id=order.order_id, task_id=order.task_id, status=order.status)
