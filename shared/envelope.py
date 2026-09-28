"""Construção do envelope lógico versionado das mensagens (docs/04 §4.2).

O schema formal (`contracts/message_envelope.schema.json`) e a validação no consumo
entram em M2-T01. Cada chamada gera um `message_id` novo: uma redelivery do broker
reentrega o mesmo dicionário, nunca passa por aqui de novo.
"""

from typing import Any

from shared.events import EventType
from shared.ids import IdPrefix, unique_id
from shared.timestamps import utc_now_iso

SCHEMA_VERSION = "1.0"

Envelope = dict[str, Any]


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
    return {
        "schema_version": SCHEMA_VERSION,
        "execution_id": execution_id,
        "message_id": unique_id(IdPrefix.MESSAGE),
        "task_id": task_id,
        "event_type": str(event_type),
        "event_seq": event_seq,
        "attempt_number": attempt_number,
        "published_at": utc_now_iso(),
        "decision_id": decision_id,
        "target": target,
        "payload": payload,
    }
