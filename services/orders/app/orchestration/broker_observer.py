"""Observação do broker usada pelo `StateBuilder` (mesma fonte para Rules e LLM).

Usa `queue.declare` passivo (AMQP): devolve na hora o número de mensagens prontas e
de consumidores da fila, sem redeclarar nada. A API HTTP de management não é usada
aqui porque suas contagens são atualizadas com atraso (intervalo de estatísticas).
"""

from dataclasses import dataclass
from typing import Protocol

from celery import Celery

from shared.messaging import Route


@dataclass(frozen=True)
class QueueStats:
    message_count: int
    consumer_count: int


class BrokerObserver(Protocol):
    def queue_stats(self, route: Route) -> QueueStats: ...


class AmqpBrokerObserver:
    def __init__(self, app: Celery) -> None:
        self._app = app

    def queue_stats(self, route: Route) -> QueueStats:
        with self._app.connection_for_read() as connection:
            channel = connection.channel()
            try:
                _, message_count, consumer_count = channel.queue_declare(
                    queue=str(route), passive=True
                )
            finally:
                channel.close()
        return QueueStats(message_count=message_count, consumer_count=consumer_count)
