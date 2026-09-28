"""Envelope lógico versionado das mensagens (docs/04 §4.2) e payloads por evento.

O contrato vive aqui (modelos pydantic) e é exportado para `contracts/*.schema.json`
por `scripts/contracts/export_schemas.py`; um teste de contrato garante que os
arquivos não divergem do código.

Mensagem fora do contrato é um problema de transporte/contrato, não de negócio: o
consumidor a rejeita sem requeue e ela segue para `tasks.dlq` (D-13).
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from shared.events import EventType
from shared.ids import IdPrefix, unique_id
from shared.timestamps import utc_now_iso

SCHEMA_VERSION = "1.0"

ISO_UTC_MS_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"

Envelope = dict[str, Any]


ExecutionId = Annotated[StrictStr, Field(pattern=r"^(PILOT|EXP)_[A-Za-z0-9]+$")]
MessageId = Annotated[StrictStr, Field(pattern=r"^MSG_[A-Za-z0-9]+$")]
TaskId = Annotated[StrictStr, Field(pattern=r"^TASK_[A-Za-z0-9]+$")]
OrderId = Annotated[StrictStr, Field(pattern=r"^ORD_[A-Za-z0-9]+$")]
DecisionId = Annotated[StrictStr, Field(pattern=r"^DEC_[A-Za-z0-9]+$")]
# Rota lógica selecionada pelo orquestrador (docs/04 §4.2).
Target = Literal["inventory.primary", "inventory.fallback"]


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MessageEnvelope(_Contract):
    schema_version: Literal["1.0"]
    execution_id: ExecutionId
    message_id: MessageId
    task_id: TaskId
    event_type: EventType
    event_seq: Annotated[StrictInt, Field(ge=1)]
    attempt_number: Annotated[StrictInt, Field(ge=1)]
    published_at: Annotated[StrictStr, Field(pattern=ISO_UTC_MS_PATTERN)]
    decision_id: DecisionId | None
    target: Target | None
    payload: dict[str, Any]


class OrderItemPayload(_Contract):
    sku: Annotated[StrictStr, Field(min_length=1)]
    quantity: Annotated[StrictInt, Field(gt=0)]


class ReservationRequestPayload(_Contract):
    """Payload de `STOCK_RESERVATION_REQUESTED` (Orders → Inventory)."""

    order_id: OrderId
    items: Annotated[list[OrderItemPayload], Field(min_length=1)]


class ReservationResultPayload(_Contract):
    """Payload de `STOCK_RESERVATION_SUCCEEDED` / `_FAILED` (Inventory → Orders)."""

    order_id: OrderId
    route: Literal["primary", "fallback"]


PAYLOAD_MODELS: dict[EventType, type[_Contract]] = {
    EventType.STOCK_RESERVATION_REQUESTED: ReservationRequestPayload,
    EventType.STOCK_RESERVATION_SUCCEEDED: ReservationResultPayload,
    EventType.STOCK_RESERVATION_FAILED: ReservationResultPayload,
}


class ContractViolation(ValueError):
    """A mensagem recebida não respeita o contrato (D-13)."""


def build_envelope(
    *,
    execution_id: str,
    task_id: str,
    event_type: EventType,
    event_seq: int,
    attempt_number: int,
    payload: dict[str, Any],
    target: str | None = None,
    decision_id: str | None = None,
) -> Envelope:
    """Monta e valida um envelope novo (sempre com `message_id` novo).

    Uma redelivery do broker reentrega a mensagem original; nunca passa por aqui.
    """
    envelope = MessageEnvelope(
        schema_version=SCHEMA_VERSION,
        execution_id=execution_id,
        message_id=unique_id(IdPrefix.MESSAGE),
        task_id=task_id,
        event_type=event_type,
        event_seq=event_seq,
        attempt_number=attempt_number,
        published_at=utc_now_iso(),
        decision_id=decision_id,
        target=target,
        payload=payload,
    )
    _validate_payload(envelope)
    return envelope.model_dump(mode="json")


def parse_message(raw: Any, accepted: set[EventType]) -> tuple[MessageEnvelope, _Contract]:
    """Valida envelope, tipo de evento aceito pelo consumidor e payload do evento."""
    try:
        envelope = MessageEnvelope.model_validate(raw)
    except ValidationError as error:
        raise ContractViolation(f"invalid envelope: {_summary(error)}") from error
    if envelope.event_type not in accepted:
        raise ContractViolation(f"unexpected event_type: {envelope.event_type}")
    return envelope, _validate_payload(envelope)


def _validate_payload(envelope: MessageEnvelope) -> _Contract:
    model = PAYLOAD_MODELS.get(envelope.event_type)
    if model is None:
        raise ContractViolation(f"no payload contract for {envelope.event_type}")
    try:
        return model.model_validate(envelope.payload)
    except ValidationError as error:
        raise ContractViolation(
            f"invalid payload for {envelope.event_type}: {_summary(error)}"
        ) from error


def _summary(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}" for item in error.errors()
    )
