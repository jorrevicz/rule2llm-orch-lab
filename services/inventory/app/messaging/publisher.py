"""Publicação de eventos do inventory-service em `orders.events`."""

from typing import Protocol

from celery import Celery

from shared.envelope import Envelope
from shared.messaging import ORDERS_HANDLE_EVENT_TASK, Route


class EventPublisher(Protocol):
    def publish(self, envelope: Envelope) -> None: ...


class CeleryEventPublisher:
    def __init__(self, app: Celery) -> None:
        self._app = app

    def publish(self, envelope: Envelope) -> None:
        self._app.send_task(
            ORDERS_HANDLE_EVENT_TASK, args=[envelope], queue=str(Route.ORDERS_EVENTS)
        )
