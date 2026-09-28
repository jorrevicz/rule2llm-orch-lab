"""Consumidores Celery do orders-service."""

from celery.exceptions import Reject
from celery.utils.log import get_task_logger

from services.orders.app.db.connection import connect
from services.orders.app.messaging.celery_app import app, settings
from services.orders.app.orchestration.event_handler import handle_inventory_event
from shared.envelope import ContractViolation, parse_message
from shared.events import EventType
from shared.messaging import ORDERS_HANDLE_EVENT_TASK

logger = get_task_logger(__name__)

ACCEPTED_EVENTS = {
    EventType.STOCK_RESERVATION_SUCCEEDED,
    EventType.STOCK_RESERVATION_FAILED,
}


@app.task(name=ORDERS_HANDLE_EVENT_TASK)
def handle_event(raw_envelope: object) -> None:
    try:
        envelope, _ = parse_message(raw_envelope, ACCEPTED_EVENTS)
    except ContractViolation as violation:
        # D-13: mensagem fora do contrato → rejeitada sem requeue → tasks.dlq.
        logger.warning("contract violation, message dead-lettered: %s", violation)
        raise Reject(str(violation), requeue=False) from violation

    connection = connect(settings.database_path)
    try:
        handle_inventory_event(connection, envelope)
    finally:
        connection.close()
