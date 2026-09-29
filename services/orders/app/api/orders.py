"""Endpoints de pedidos: `POST /orders` e `GET /orders/{order_id}`."""

import logging
import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from services.orders.app.api.schemas import CreateOrderRequest, OrderResponse
from services.orders.app.db.connection import connect
from services.orders.app.db.repositories import create_order_with_task, get_order
from services.orders.app.orchestration.coordination import Coordination
from services.orders.app.settings import Settings
from shared.canonical_json import canonical_json
from shared.structured_logging import correlated
from shared.timestamps import utc_now_iso

router = APIRouter()
logger = logging.getLogger(__name__)


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_coordination(request: Request) -> Coordination:
    return request.app.state.coordination


def get_connection(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Iterator[sqlite3.Connection]:
    # O FastAPI abre a dependência numa thread do pool e roda o endpoint (síncrono) em
    # outra; a conexão é de uma requisição só e nunca é usada ao mesmo tempo por duas
    # threads. Com check_same_thread, requisições simultâneas falhavam com 500 (M6-T11).
    connection = connect(settings.database_path, check_same_thread=False)
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
    # Ponto de decisão "início da tarefa" (docs/06 §6.7).
    outcome = coordination.orchestrator.handle_decision_point(connection, created.task_id)
    logger.info(
        "order accepted; initial decision %s/%s",
        outcome.executed.action,
        outcome.executed.reason_code,
        extra=correlated(
            order_id=created.order_id,
            task_id=created.task_id,
            state_id=outcome.state_id,
            decision_id=outcome.decision_id,
            outcome=outcome.executed.action,
        ),
    )
    return OrderResponse(order_id=created.order_id, task_id=created.task_id, status=created.status)


@router.get("/orders/{order_id}", response_model=OrderResponse)
def read_order(order_id: str, connection: Connection) -> OrderResponse:
    order = get_order(connection, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="order not found")
    return OrderResponse(order_id=order.order_id, task_id=order.task_id, status=order.status)
