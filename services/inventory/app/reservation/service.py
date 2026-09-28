"""Processamento de uma solicitação de reserva (RF-006, RF-007, RF-010).

Ordem: reservar → persistir (reserva + mensagem processada) → publicar o resultado.
A publicação acontece só depois do commit, para que Orders nunca receba sucesso de
uma reserva que não foi persistida.
"""

import sqlite3

from services.inventory.app.db.repositories import record_reservation
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


def process_reservation_request(
    connection: sqlite3.Connection,
    request: MessageEnvelope,
    payload: ReservationRequestPayload,
    publisher: EventPublisher,
) -> Envelope:
    if request.target != Route.INVENTORY_PRIMARY:
        # A rota inventory.fallback é implementada em M4-T08.
        raise ValueError(f"unsupported target: {request.target!r}")

    items = [item.model_dump() for item in payload.items]
    outcome = reserve_primary(items)
    record_reservation(
        connection,
        message_id=request.message_id,
        task_id=request.task_id,
        order_id=payload.order_id,
        route=outcome.route,
        items_json=canonical_json(items),
        now=utc_now_iso(),
    )
    event = _succeeded_event(request, payload.order_id, outcome.route)
    publisher.publish(event)
    return event


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
