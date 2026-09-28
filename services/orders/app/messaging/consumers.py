"""Consumidores Celery do orders-service."""

from services.orders.app.db.connection import connect
from services.orders.app.messaging.celery_app import app, settings
from services.orders.app.orchestration.event_handler import handle_inventory_event
from shared.envelope import Envelope
from shared.messaging import ORDERS_HANDLE_EVENT_TASK


@app.task(name=ORDERS_HANDLE_EVENT_TASK)
def handle_event(envelope: Envelope) -> None:
    connection = connect(settings.database_path)
    try:
        handle_inventory_event(connection, envelope)
    finally:
        connection.close()
