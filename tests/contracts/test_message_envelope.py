import json

import pytest

from scripts.contracts.export_schemas import (
    ENVELOPE_SCHEMA_PATH,
    PAYLOADS_SCHEMA_PATH,
    envelope_schema,
    payloads_schema,
    render,
)
from shared.envelope import ContractViolation, build_envelope, parse_message
from shared.events import EventType

REQUESTED = EventType.STOCK_RESERVATION_REQUESTED
SUCCEEDED = EventType.STOCK_RESERVATION_SUCCEEDED


def _request(**overrides) -> dict:
    envelope = build_envelope(
        execution_id="PILOT_0001",
        task_id="TASK_000187",
        event_type=REQUESTED,
        event_seq=2,
        attempt_number=1,
        target="inventory.primary",
        payload={"order_id": "ORD_000187", "items": [{"sku": "SKU-001", "quantity": 2}]},
    )
    envelope.update(overrides)
    return envelope


def test_committed_schemas_match_the_code():
    # Se falhar: rode `python -m scripts.contracts.export_schemas` e revise o impacto.
    assert ENVELOPE_SCHEMA_PATH.read_text(encoding="utf-8") == render(envelope_schema())
    assert PAYLOADS_SCHEMA_PATH.read_text(encoding="utf-8") == render(payloads_schema())


def test_envelope_schema_requires_every_field():
    schema = json.loads(ENVELOPE_SCHEMA_PATH.read_text(encoding="utf-8"))

    assert set(schema["required"]) == {
        "schema_version",
        "execution_id",
        "message_id",
        "task_id",
        "event_type",
        "event_seq",
        "attempt_number",
        "published_at",
        "decision_id",
        "target",
        "payload",
    }
    assert schema["additionalProperties"] is False


def test_built_envelope_is_valid_and_json_serializable():
    envelope = _request()

    parsed, payload = parse_message(json.loads(json.dumps(envelope)), {REQUESTED})

    assert parsed.message_id == envelope["message_id"]
    assert payload.order_id == "ORD_000187"


def test_each_built_envelope_has_a_new_message_id():
    assert _request()["message_id"] != _request()["message_id"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"schema_version": "2.0"},
        {"execution_id": "RUN_0001"},
        {"message_id": "0192"},
        {"task_id": "ORD_000187"},
        {"event_type": "STOCK_RESERVED"},
        {"event_seq": 0},
        {"event_seq": "2"},
        {"attempt_number": 0},
        {"published_at": "2026-08-27 12:00:00"},
        {"decision_id": "0091"},
        {"target": "service_c"},
        {"target": "orders.events"},
        {"shell": "rm -rf /"},
    ],
    ids=lambda overrides: next(iter(overrides)),
)
def test_invalid_envelope_is_a_contract_violation(overrides):
    with pytest.raises(ContractViolation, match="invalid envelope"):
        parse_message(_request(**overrides), {REQUESTED})


@pytest.mark.parametrize(
    "payload",
    [
        {"order_id": "ORD_000187", "items": []},
        {"order_id": "ORD_000187", "items": [{"sku": "SKU-001", "quantity": 0}]},
        {"order_id": "ORD_000187", "items": [{"sku": "SKU-001", "quantity": 2, "price": 1}]},
        {"items": [{"sku": "SKU-001", "quantity": 2}]},
        {"order_id": "ORD_000187", "items": [{"sku": "SKU-001", "quantity": 2}], "cmd": "x"},
    ],
    ids=["empty-items", "zero-quantity", "extra-item-field", "missing-order", "extra-field"],
)
def test_invalid_payload_is_a_contract_violation(payload):
    with pytest.raises(ContractViolation, match="invalid payload"):
        parse_message(_request(payload=payload), {REQUESTED})


def test_event_type_not_accepted_by_consumer_is_a_contract_violation():
    with pytest.raises(ContractViolation, match="unexpected event_type"):
        parse_message(_request(), {SUCCEEDED})


def test_non_dict_message_is_a_contract_violation():
    with pytest.raises(ContractViolation):
        parse_message("not an envelope", {REQUESTED})


def test_producer_cannot_build_an_envelope_outside_the_contract():
    with pytest.raises(ContractViolation):
        build_envelope(
            execution_id="PILOT_0001",
            task_id="TASK_000187",
            event_type=SUCCEEDED,
            event_seq=3,
            attempt_number=1,
            payload={"order_id": "ORD_000187", "route": "service_c"},
        )
