"""Consumidores Celery do orders-service."""

from celery import Task
from celery.exceptions import Reject
from celery.utils.log import get_task_logger

from services.orders.app.db.connection import connect
from services.orders.app.messaging.celery_app import app, settings
from services.orders.app.orchestration.event_handler import handle_inventory_event
from shared.envelope import ContractViolation, parse_message
from shared.events import EventType
from shared.messaging import ORDERS_HANDLE_EVENT_TASK
from shared.structured_logging import correlated

logger = get_task_logger(__name__)

ACCEPTED_EVENTS = {
    EventType.STOCK_RESERVATION_SUCCEEDED,
    EventType.STOCK_RESERVATION_FAILED,
}


@app.task(name=ORDERS_HANDLE_EVENT_TASK, bind=True)
def handle_event(self: Task, raw_envelope: object) -> None:
    redelivered = bool((self.request.delivery_info or {}).get("redelivered", False))
    try:
        envelope, _ = parse_message(raw_envelope, ACCEPTED_EVENTS)
    except ContractViolation as violation:
        # D-13: mensagem fora do contrato → rejeitada sem requeue → tasks.dlq.
        logger.warning(
            "contract violation, message dead-lettered: %s",
            violation,
            extra=correlated(outcome="dead_lettered", redelivered=redelivered),
        )
        raise Reject(str(violation), requeue=False) from violation

    connection = connect(settings.database_path)
    try:
        outcome = handle_inventory_event(connection, envelope, redelivered=redelivered)
    finally:
        connection.close()
    logger.info(
        "inventory event handled: %s (changed_state=%s)",
        outcome.status,
        outcome.changed_state,
        extra=correlated(
            outcome=outcome.status,
            task_id=envelope.task_id,
            message_id=envelope.message_id,
            event_type=envelope.event_type,
            event_seq=outcome.event_seq,
            attempt_number=envelope.attempt_number,
            redelivered=redelivered,
        ),
    )
