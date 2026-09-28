"""Consumidores Celery do inventory-service."""

from services.inventory.app.db.connection import connect
from services.inventory.app.messaging.celery_app import app, settings
from services.inventory.app.messaging.publisher import CeleryEventPublisher
from services.inventory.app.reservation.service import process_reservation_request
from shared.envelope import Envelope
from shared.messaging import INVENTORY_RESERVE_TASK

_publisher = CeleryEventPublisher(app)


@app.task(name=INVENTORY_RESERVE_TASK)
def reserve_stock(envelope: Envelope) -> None:
    connection = connect(settings.database_path)
    try:
        process_reservation_request(connection, envelope, _publisher)
    finally:
        connection.close()
