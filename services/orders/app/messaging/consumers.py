"""Consumidores Celery do orders-service (fila `orders.events`).

- `orders.handle_inventory_event`: eventos do Inventory (contrato validado; D-13);
- `orders.timeout_check`: verificação de timeout operacional agendada a cada despacho;
- `orders.dispatch_attempt`: despacho da nova tentativa, `retry_delay_ms` após o `RETRY`;
- `orders.reevaluate`: fim do `WAIT`, `wait_delay_ms` depois, e novo ponto de decisão.

Quando um evento ou timeout pede decisão, o ponto de decisão é aberto depois do
commit do registro, pelo Orchestrator (mesmo fluxo para Rules e LLM).
"""

from functools import cache

from celery import Task
from celery.exceptions import Reject
from celery.utils.log import get_task_logger

from services.orders.app.db.connection import connect
from services.orders.app.messaging.celery_app import app, settings
from services.orders.app.orchestration.coordination import Coordination, build_coordination
from services.orders.app.orchestration.event_handler import handle_inventory_event
from services.orders.app.orchestration.timeouts import register_timeout
from services.orders.app.orchestration.waits import finish_wait
from shared.config import load_experiment_config
from shared.envelope import ContractViolation, parse_message
from shared.events import EventType
from shared.messaging import (
    ORDERS_DISPATCH_ATTEMPT_TASK,
    ORDERS_HANDLE_EVENT_TASK,
    ORDERS_REEVALUATE_TASK,
    ORDERS_TIMEOUT_CHECK_TASK,
)
from shared.structured_logging import correlated

logger = get_task_logger(__name__)

ACCEPTED_EVENTS = {
    EventType.STOCK_RESERVATION_SUCCEEDED,
    EventType.STOCK_RESERVATION_FAILED,
}


@cache
def _coordination() -> Coordination:
    return build_coordination(settings, load_experiment_config(), app)


def _decide(connection, task_id: str) -> None:
    outcome = _coordination().orchestrator.handle_decision_point(connection, task_id)
    logger.info(
        "decision point: %s/%s",
        outcome.executed.action,
        outcome.executed.reason_code,
        extra=correlated(
            task_id=task_id,
            state_id=outcome.state_id,
            decision_id=outcome.decision_id,
            outcome=outcome.executed.action,
        ),
    )


@app.task(name=ORDERS_HANDLE_EVENT_TASK, bind=True)
def handle_event(self: Task, raw_envelope: object) -> None:
    redelivered = bool((self.request.delivery_info or {}).get("redelivered", False))
    try:
        envelope, _ = parse_message(raw_envelope, ACCEPTED_EVENTS)
    except ContractViolation as violation:
        # D-13: mensagem fora do contrato → rejeitada sem requeue → tasks.dlq.
        logger.warning(
            "contract violation, message dead-lettered: %s",
            violation,
            extra=correlated(outcome="dead_lettered", redelivered=redelivered),
        )
        raise Reject(str(violation), requeue=False) from violation

    connection = connect(settings.database_path)
    try:
        outcome = handle_inventory_event(connection, envelope, redelivered=redelivered)
        logger.info(
            "inventory event handled: %s (changed_state=%s)",
            outcome.status,
            outcome.changed_state,
            extra=correlated(
                outcome=outcome.status,
                task_id=envelope.task_id,
                message_id=envelope.message_id,
                event_type=envelope.event_type,
                event_seq=outcome.event_seq,
                attempt_number=envelope.attempt_number,
                redelivered=redelivered,
            ),
        )
        if outcome.decision_required:
            _decide(connection, envelope.task_id)
    finally:
        connection.close()


@app.task(name=ORDERS_TIMEOUT_CHECK_TASK)
def timeout_check(task_id: str, request_message_id: str) -> None:
    connection = connect(settings.database_path)
    try:
        if not register_timeout(connection, task_id=task_id, request_message_id=request_message_id):
            return
        logger.info(
            "inventory timeout",
            extra=correlated(
                task_id=task_id, message_id=request_message_id, event_type=EventType.INVENTORY_TIMEOUT
            ),
        )
        _decide(connection, task_id)
    finally:
        connection.close()


@app.task(name=ORDERS_DISPATCH_ATTEMPT_TASK)
def dispatch_attempt(task_id: str, decision_id: str) -> None:
    connection = connect(settings.database_path)
    try:
        executor = _coordination().orchestrator.executor
        dispatched = executor.dispatch_scheduled_attempt(connection, task_id=task_id, decision_id=decision_id)
        logger.info(
            "scheduled attempt %s",
            "dispatched" if dispatched else "skipped",
            extra=correlated(task_id=task_id, decision_id=decision_id, outcome="dispatched" if dispatched else "skipped"),
        )
    finally:
        connection.close()


@app.task(name=ORDERS_REEVALUATE_TASK)
def reevaluate(task_id: str, wait_count: int) -> None:
    connection = connect(settings.database_path)
    try:
        if not finish_wait(connection, task_id=task_id, wait_count=wait_count):
            return
        logger.info("wait finished", extra=correlated(task_id=task_id, event_type=EventType.WAIT_FINISHED))
        _decide(connection, task_id)
    finally:
        connection.close()
