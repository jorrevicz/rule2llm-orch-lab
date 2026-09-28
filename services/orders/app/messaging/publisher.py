"""Publicação de comandos do orders-service para o inventory-service."""

from typing import Protocol

from celery import Celery

from shared.envelope import Envelope
from shared.messaging import INVENTORY_RESERVE_TASK, Route


class CommandPublisher(Protocol):
    def publish(self, envelope: Envelope, route: Route) -> None: ...


class CeleryCommandPublisher:
    def __init__(self, app: Celery) -> None:
        self._app = app

    def publish(self, envelope: Envelope, route: Route) -> None:
        self._app.send_task(INVENTORY_RESERVE_TASK, args=[envelope], queue=str(route))
