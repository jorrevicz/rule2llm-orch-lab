"""Consumidores Celery do inventory-service."""

from celery.exceptions import Reject
from celery.utils.log import get_task_logger

from services.inventory.app.db.connection import connect
from services.inventory.app.messaging.celery_app import app, settings
from services.inventory.app.messaging.publisher import CeleryEventPublisher
from services.inventory.app.reservation.service import process_reservation_request
from shared.envelope import ContractViolation, parse_message
from shared.events import EventType
from shared.messaging import INVENTORY_RESERVE_TASK

logger = get_task_logger(__name__)

_publisher = CeleryEventPublisher(app)


@app.task(name=INVENTORY_RESERVE_TASK)
def reserve_stock(raw_envelope: object) -> None:
    try:
        envelope, payload = parse_message(raw_envelope, {EventType.STOCK_RESERVATION_REQUESTED})
    except ContractViolation as violation:
        # D-13: mensagem fora do contrato → rejeitada sem requeue → tasks.dlq.
        logger.warning("contract violation, message dead-lettered: %s", violation)
        raise Reject(str(violation), requeue=False) from violation

    connection = connect(settings.database_path)
    try:
        process_reservation_request(connection, envelope, payload, _publisher)
    finally:
        connection.close()
