"""Processamento de uma solicitação de reserva (RF-006 a RF-010).

Numa única transação: idempotência de transporte (`message_id`) → idempotência de
negócio (`task_id`) → reservar → gravar reserva, mensagem processada e resposta.
A resposta é publicada só depois do commit, para que o Orders nunca receba
sucesso de uma reserva não persistida.

- Redelivery da mesma mensagem (mesmo `message_id`): não reprocessa; reemite a
  resposta gravada, com o MESMO `message_id`, que o Orders deduplica. Isso também
  recupera uma resposta perdida entre o commit e a publicação.
- Nova tentativa lógica (novo `message_id`, mesmo `task_id`) de tarefa já
  reservada: não cria segunda reserva; responde com o resultado da reserva
  existente, numa resposta nova (é outra mensagem).
"""

import json
import sqlite3
from dataclasses import dataclass
from enum import StrEnum

from services.inventory.app.db.connection import transaction
from services.inventory.app.db.models import ProcessingResult
from services.inventory.app.db.repositories import (
    find_processed_message,
    find_reservation,
    insert_processed_message,
    insert_reservation,
)
from services.inventory.app.messaging.publisher import EventPublisher
from services.inventory.app.reservation.primary import reserve_primary
from shared.canonical_json import canonical_json
from shared.envelope import (
    Envelope,
    MessageEnvelope,
    ReservationRequestPayload,
    build_envelope,
)
from shared.events import EventType
from shared.messaging import Route
from shared.timestamps import utc_now_iso


class RequestOutcome(StrEnum):
    RESERVED = "reserved"
    DUPLICATE_MESSAGE = "duplicate_message"  # redelivery: mesma mensagem já processada
    ALREADY_RESERVED = "already_reserved"    # nova tentativa de tarefa já reservada


@dataclass(frozen=True)
class RequestResult:
    outcome: RequestOutcome
    reply: Envelope


def process_reservation_request(
    connection: sqlite3.Connection,
    request: MessageEnvelope,
    payload: ReservationRequestPayload,
    publisher: EventPublisher,
) -> RequestResult:
    if request.target != Route.INVENTORY_PRIMARY:
        # A rota inventory.fallback é implementada em M4-T08.
        raise ValueError(f"unsupported target: {request.target!r}")

    now = utc_now_iso()
    with transaction(connection):
        previous = find_processed_message(connection, request.message_id)
        if previous is not None:
            result = RequestResult(RequestOutcome.DUPLICATE_MESSAGE, json.loads(previous.response_json))
        else:
            result = _reserve(connection, request, payload, now)
    publisher.publish(result.reply)
    return result


def _reserve(
    connection: sqlite3.Connection,
    request: MessageEnvelope,
    payload: ReservationRequestPayload,
    now: str,
) -> RequestResult:
    existing = find_reservation(connection, request.task_id)
    if existing is not None:
        outcome = RequestOutcome.ALREADY_RESERVED
        reply = _succeeded_event(request, existing.order_id, existing.route)
    else:
        outcome = RequestOutcome.RESERVED
        items = [item.model_dump() for item in payload.items]
        reservation = reserve_primary(items)
        reply = _succeeded_event(request, payload.order_id, reservation.route)
        insert_reservation(
            connection,
            task_id=request.task_id,
            order_id=payload.order_id,
            route=reservation.route,
            items_json=canonical_json(items),
            now=now,
        )
    insert_processed_message(
        connection,
        message_id=request.message_id,
        task_id=request.task_id,
        event_type=request.event_type,
        result=ProcessingResult.SUCCEEDED,
        response_json=canonical_json(reply),
        now=now,
    )
    return RequestResult(outcome, reply)


def _succeeded_event(request: MessageEnvelope, order_id: str, route: str) -> Envelope:
    return build_envelope(
        execution_id=request.execution_id,
        task_id=request.task_id,
        event_type=EventType.STOCK_RESERVATION_SUCCEEDED,
        # D-16: o Inventory não numera a trajetória; repete o event_seq da solicitação
        # respondida (correlação). O Orders atribui a posição ao registrar o evento.
        event_seq=request.event_seq,
        attempt_number=request.attempt_number,
        decision_id=request.decision_id,
        target=request.target,
        payload={"order_id": order_id, "route": str(route)},
    )
