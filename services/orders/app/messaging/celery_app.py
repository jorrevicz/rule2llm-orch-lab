"""Aplicação Celery do orders-service (publica em inventory.* e consome orders.events)."""

from celery import Celery

from services.orders.app.settings import load_settings
from shared.messaging import configure_celery

app = Celery("orders-service")
configure_celery(app, load_settings().broker_url)
