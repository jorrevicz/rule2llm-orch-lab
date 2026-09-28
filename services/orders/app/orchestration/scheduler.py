"""Agendamento das tarefas internas de coordenação do orders-service.

Implementadas como tarefas Celery com atraso (`countdown`) na fila `orders.events`,
que também recebe "eventos internos de coordenação" (docs/04 §4.1). Celery só
transporta e executa: o que fazer em cada uma é decidido pelo Orchestrator.
"""

from typing import Protocol

from celery import Celery

from shared.messaging import (
    ORDERS_DISPATCH_ATTEMPT_TASK,
    ORDERS_REEVALUATE_TASK,
    ORDERS_TIMEOUT_CHECK_TASK,
    Route,
)


class TaskScheduler(Protocol):
    def schedule_timeout_check(self, *, task_id: str, request_message_id: str, delay_ms: int) -> None: ...

    def schedule_dispatch(self, *, task_id: str, decision_id: str, delay_ms: int) -> None: ...

    def schedule_reevaluation(self, *, task_id: str, wait_count: int, delay_ms: int) -> None: ...


class CeleryTaskScheduler:
    def __init__(self, app: Celery) -> None:
        self._app = app

    def schedule_timeout_check(self, *, task_id: str, request_message_id: str, delay_ms: int) -> None:
        self._send(ORDERS_TIMEOUT_CHECK_TASK, delay_ms, task_id=task_id, request_message_id=request_message_id)

    def schedule_dispatch(self, *, task_id: str, decision_id: str, delay_ms: int) -> None:
        self._send(ORDERS_DISPATCH_ATTEMPT_TASK, delay_ms, task_id=task_id, decision_id=decision_id)

    def schedule_reevaluation(self, *, task_id: str, wait_count: int, delay_ms: int) -> None:
        self._send(ORDERS_REEVALUATE_TASK, delay_ms, task_id=task_id, wait_count=wait_count)

    def _send(self, task_name: str, delay_ms: int, **kwargs: object) -> None:
        self._app.send_task(
            task_name, kwargs=kwargs, queue=str(Route.ORDERS_EVENTS), countdown=delay_ms / 1000
        )
