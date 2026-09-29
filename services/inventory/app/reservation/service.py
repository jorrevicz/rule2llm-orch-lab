"""Processamento de uma solicitação de reserva (RF-006 a RF-010).

Antes da transação, o custo de processamento simulado (D-22). Depois, numa única
transação: idempotência de transporte (`message_id`) → idempotência de negócio
(`task_id`) → reservar → gravar reserva, mensagem processada e resposta.
A resposta é publicada só depois do commit, para que o Orders nunca receba
sucesso de uma reserva não persistida.

- Redelivery da mesma mensagem (mesmo `message_id`): não reprocessa; reemite a
  resposta gravada, com o MESMO `message_id`, que o Orders deduplica. Isso também
  recupera uma resposta perdida entre o commit e a publicação.
- Nova tentativa lógica (novo `message_id`, mesmo `task_id`) de tarefa já
  reservada: não cria segunda reserva; responde com o resultado da reserva
  existente, numa resposta nova (é outra mensagem).
- Pedido com SKU fora do catálogo do Inventory (D-03): não reserva; responde
  `STOCK_RESERVATION_FAILED / invalid_data`, em qualquer rota. A resposta também fica
  em `processed_messages`, para ser reemitida numa redelivery.
"""

import json
import sqlite3
from dataclasses import dataclass
from enum import StrEnum

from services.inventory.app.db.connection import transaction
from services.inventory.app.db.models import ProcessingResult, ReservationRoute
from services.inventory.app.db.repositories import (
    find_processed_message,
    find_reservation,
    insert_processed_message,
    insert_reservation,
    unknown_skus,
)
from services.inventory.app.messaging.publisher import EventPublisher
from services.inventory.app.reservation.fallback import reserve_fallback
from services.inventory.app.reservation.primary import ReservationOutcome, reserve_primary
from services.inventory.app.reservation.processing import ProcessingSimulator
from shared.canonical_json import canonical_json
from shared.envelope import (
    Envelope,
    MessageEnvelope,
    ReservationRequestPayload,
    build_envelope,
)
from shared.events import EventType
from shared.messaging import Route
from shared.task import TaskResult
from shared.timestamps import utc_now_iso


class RequestOutcome(StrEnum):
    RESERVED = "reserved"
    DUPLICATE_MESSAGE = "duplicate_message"  # redelivery: mesma mensagem já processada
    ALREADY_RESERVED = "already_reserved"    # nova tentativa de tarefa já reservada
    INVALID_DATA = "invalid_data"            # SKU fora do catálogo (D-03)


@dataclass(frozen=True)
class RequestResult:
    outcome: RequestOutcome
    reply: Envelope


def process_reservation_request(
    connection: sqlite3.Connection,
    request: MessageEnvelope,
    payload: ReservationRequestPayload,
    publisher: EventPublisher,
    simulator: ProcessingSimulator,
) -> RequestResult:
    simulator.before_reservation(request)  # fora da transação: não segura o lock
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
    items = [item.model_dump() for item in payload.items]
    result = ProcessingResult.SUCCEEDED
    if existing is not None:
        outcome = RequestOutcome.ALREADY_RESERVED
        reply = _succeeded_event(request, existing.order_id, existing.route)
    elif unknown_skus(connection, [item["sku"] for item in items]):
        outcome = RequestOutcome.INVALID_DATA
        result = ProcessingResult.FAILED
        reply = _failed_event(request, payload.order_id, TaskResult.INVALID_DATA)
    else:
        outcome = RequestOutcome.RESERVED
        reservation = _reserve_by_route(request.target, items)
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
        result=result,
        response_json=canonical_json(reply),
        now=now,
    )
    return RequestResult(outcome, reply)


def _route_name(target: str) -> str:
    return ReservationRoute.FALLBACK if target == Route.INVENTORY_FALLBACK else ReservationRoute.PRIMARY


def _reserve_by_route(target: str, items: list[dict]) -> ReservationOutcome:
    """Rota escolhida pelo target do envelope (decidido pelo orquestrador)."""
    if target == Route.INVENTORY_FALLBACK:
        return reserve_fallback(items)
    return reserve_primary(items)


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


def _failed_event(request: MessageEnvelope, order_id: str, reason: TaskResult) -> Envelope:
    return build_envelope(
        execution_id=request.execution_id,
        task_id=request.task_id,
        event_type=EventType.STOCK_RESERVATION_FAILED,
        event_seq=request.event_seq,  # D-16: correlação com a solicitação respondida
        attempt_number=request.attempt_number,
        decision_id=request.decision_id,
        target=request.target,
        payload={"order_id": order_id, "route": str(_route_name(request.target)), "failure_reason": str(reason)},
    )
