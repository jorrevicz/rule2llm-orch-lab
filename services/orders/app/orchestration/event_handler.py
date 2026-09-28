"""Tratamento dos eventos recebidos em `orders.events` (RF-011).

Numa única transação: deduplicação por `message_id` (`processed_events`) →
numeração na trajetória (D-16) → registro em `task_events` → aplicação do evento.

- Evento repetido (mesmo `message_id`, ex.: resposta reemitida pelo Inventory numa
  redelivery): não é reaplicado e não consome `event_seq`; entra na trajetória com o
  `event_seq` da primeira ocorrência e `redelivered = 1`.
- Evento terminal de sucesso conclui tarefa e pedido sem consultar o DecisionEngine
  (piloto §5.2) e registra `TASK_COMPLETED`. Um sucesso de tentativa antiga também
  conclui: a reserva existe (idempotência de negócio).
- Falha reportada para a solicitação corrente grava `last_result` (`transient_error`,
  `invalid_data`, ou `fallback_failed` na rota de fallback — D-15) e pede um ponto de
  decisão, que o chamador abre após o commit. Falha de solicitação já superada só é
  registrada.
"""

import sqlite3
from dataclasses import dataclass
from enum import StrEnum

from services.orders.app.db.connection import transaction
from services.orders.app.db.models import TERMINAL_TASK_STATUSES
from services.orders.app.db.repositories import (
    complete_task,
    insert_processed_event,
    is_event_processed,
    set_last_result,
)
from services.orders.app.db.trajectory import (
    EventSource,
    advance_event_seq,
    event_seq_of_message,
    latest_request,
    record_internal_event,
    record_message,
)
from shared.envelope import MessageEnvelope
from shared.events import EventType
from shared.messaging import Route
from shared.task import TaskResult
from shared.timestamps import utc_now_iso

HANDLED_EVENTS = frozenset(
    {EventType.STOCK_RESERVATION_SUCCEEDED, EventType.STOCK_RESERVATION_FAILED}
)


class UnsupportedEventError(ValueError):
    pass


class EventStatus(StrEnum):
    RECORDED = "recorded"            # evento novo, registrado na trajetória
    DUPLICATE = "duplicate"          # mesmo message_id já processado: não reaplicado
    UNKNOWN_TASK = "unknown_task"    # tarefa inexistente neste orders.db: ignorado


@dataclass(frozen=True)
class EventOutcome:
    status: EventStatus
    event_seq: int | None = None     # posição atribuída pelo Orders (só se RECORDED)
    changed_state: bool = False      # houve transição de estado da tarefa
    decision_required: bool = False  # abrir ponto de decisão após o commit


def handle_inventory_event(
    connection: sqlite3.Connection, event: MessageEnvelope, *, redelivered: bool = False
) -> EventOutcome:
    """Deduplica, registra e aplica um evento já validado.

    `redelivered` é o flag do broker (reentrega da mesma mensagem).
    """
    if event.event_type not in HANDLED_EVENTS:
        raise UnsupportedEventError(f"event type not handled: {event.event_type}")

    now = utc_now_iso()
    with transaction(connection):
        if is_event_processed(connection, event.message_id):
            _record_repeated(connection, event, now)
            return EventOutcome(EventStatus.DUPLICATE)
        event_seq = advance_event_seq(connection, task_id=event.task_id, now=now)
        if event_seq is None:
            return EventOutcome(EventStatus.UNKNOWN_TASK)
        insert_processed_event(connection, event=event, now=now)
        record_message(
            connection,
            event,
            event_seq=event_seq,
            service=EventSource.INVENTORY,
            redelivered=redelivered,
            now=now,
        )
        if event.event_type == EventType.STOCK_RESERVATION_SUCCEEDED:
            changed = complete_task(connection, task_id=event.task_id, now=now)
            if changed:
                record_internal_event(
                    connection, task_id=event.task_id, event_type=EventType.TASK_COMPLETED, now=now
                )
            return EventOutcome(EventStatus.RECORDED, event_seq=event_seq, changed_state=changed)
        decision_required = _register_failure(connection, event, now)
    return EventOutcome(EventStatus.RECORDED, event_seq=event_seq, decision_required=decision_required)


def _register_failure(connection: sqlite3.Connection, event: MessageEnvelope, now: str) -> bool:
    task = connection.execute(
        "SELECT status FROM tasks WHERE task_id = ?", (event.task_id,)
    ).fetchone()
    request = latest_request(connection, event.task_id)
    is_current = request is not None and request["event_seq"] == event.event_seq
    if task["status"] in TERMINAL_TASK_STATUSES or not is_current:
        return False
    if event.target == Route.INVENTORY_FALLBACK:
        result = TaskResult.FALLBACK_FAILED
    else:
        result = TaskResult(event.payload["failure_reason"])
    set_last_result(connection, task_id=event.task_id, result=result, now=now)
    return True


def _record_repeated(connection: sqlite3.Connection, event: MessageEnvelope, now: str) -> None:
    original_seq = event_seq_of_message(connection, event.message_id)
    if original_seq is None:
        return
    record_message(
        connection,
        event,
        event_seq=original_seq,
        service=EventSource.INVENTORY,
        redelivered=True,
        now=now,
    )
