"""Consumidores Celery do inventory-service."""

from celery import Task
from celery.exceptions import Reject
from celery.utils.log import get_task_logger

from services.inventory.app.db.connection import connect
from services.inventory.app.messaging.celery_app import app, settings
from services.inventory.app.messaging.publisher import CeleryEventPublisher
from services.inventory.app.reservation.service import process_reservation_request
from shared.envelope import ContractViolation, parse_message
from shared.events import EventType
from shared.messaging import INVENTORY_RESERVE_TASK
from shared.structured_logging import correlated

logger = get_task_logger(__name__)

_publisher = CeleryEventPublisher(app)


@app.task(name=INVENTORY_RESERVE_TASK, bind=True)
def reserve_stock(self: Task, raw_envelope: object) -> None:
    # Flag do broker: a MESMA mensagem está sendo entregue de novo (não é RETRY).
    redelivered = bool((self.request.delivery_info or {}).get("redelivered", False))
    try:
        envelope, payload = parse_message(raw_envelope, {EventType.STOCK_RESERVATION_REQUESTED})
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
        result = process_reservation_request(connection, envelope, payload, _publisher)
    finally:
        connection.close()
    logger.info(
        "reservation request handled: %s",
        result.outcome,
        extra=correlated(
            outcome=result.outcome,
            task_id=envelope.task_id,
            message_id=envelope.message_id,
            event_type=envelope.event_type,
            event_seq=envelope.event_seq,
            attempt_number=envelope.attempt_number,
            redelivered=redelivered,
        ),
    )
