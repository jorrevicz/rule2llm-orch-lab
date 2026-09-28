"""Aplicação Celery do inventory-service.

Consome inventory.primary / inventory.fallback e publica em orders.events.
"""

from celery import Celery
from celery.signals import worker_init

from services.inventory.app.db.connection import init_database
from services.inventory.app.settings import load_settings
from shared.messaging import configure_celery

settings = load_settings()

app = Celery("inventory-service", include=["services.inventory.app.messaging.consumers"])
configure_celery(app, settings.broker_url)


@worker_init.connect
def _prepare_database(**_: object) -> None:
    init_database(settings.database_path)
