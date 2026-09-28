"""Aplicação Celery do orders-service.

Publica em inventory.* e consome orders.events.
"""

from celery import Celery
from celery.signals import worker_init

from services.orders.app.db.connection import init_database
from services.orders.app.settings import load_settings
from shared.messaging import configure_celery

settings = load_settings()

app = Celery("orders-service", include=["services.orders.app.messaging.consumers"])
configure_celery(app, settings.broker_url)


@worker_init.connect
def _prepare_database(**_: object) -> None:
    init_database(settings.database_path)
