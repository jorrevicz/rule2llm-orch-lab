"""Aplicação Celery do inventory-service.

Consome inventory.primary / inventory.fallback e publica em orders.events.
"""

from celery import Celery

from services.inventory.app.settings import load_settings
from shared.messaging import configure_celery

app = Celery("inventory-service")
configure_celery(app, load_settings().broker_url)
