import json

import pytest
from pydantic import ValidationError

from scripts.contracts.export_schemas import DECISION_SCHEMA_PATH, decision_schema, render
from shared.decision import (
    INVALID_DECISION_ABORT,
    Action,
    Decision,
    ProposedDecision,
    ValidationErrorCode,
    ValidationResult,
    resolve_action,
)


def test_committed_schema_matches_the_code():
    # Se falhar: rode `python -m scripts.contracts.export_schemas` e revise o impacto.
    assert DECISION_SCHEMA_PATH.read_text(encoding="utf-8") == render(decision_schema())


def test_action_space_is_exactly_the_five_actions():
    schema = json.loads(DECISION_SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$defs"]["Action"]["enum"] == ["CONTINUE", "RETRY", "WAIT", "FALLBACK", "ABORT"]
    assert set(schema["required"]) == {"action", "target", "reason_code"}
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize(
    "decision",
    [
        {"action": "CONTINUE", "target": "inventory.primary", "reason_code": "NORMAL_FLOW"},
        {"action": "RETRY", "target": "inventory.primary", "reason_code": "TRANSIENT_RETRY"},
        {"action": "FALLBACK", "target": "inventory.fallback", "reason_code": "PRIMARY_EXHAUSTED"},
        {"action": "WAIT", "target": None, "reason_code": "QUEUE_PRESSURE"},
        {"action": "ABORT", "target": None, "reason_code": "ATTEMPTS_EXHAUSTED"},
    ],
)
def test_valid_decisions(decision):
    assert Decision.model_validate(decision).action == decision["action"]


@pytest.mark.parametrize(
    "decision",
    [
        {"action": "REDIRECT", "target": "inventory.primary", "reason_code": "X"},
        {"action": "PARALLELIZE", "target": None, "reason_code": "X"},
        {"action": "RETRY", "target": "service_c", "reason_code": "RETRY"},
        {"action": "RETRY", "target": None, "reason_code": "TRANSIENT_RETRY"},
        {"action": "WAIT", "target": "inventory.primary", "reason_code": "QUEUE_PRESSURE"},
        {"action": "ABORT", "target": None, "reason_code": ""},
        {"action": "ABORT", "target": None, "reason_code": "X", "command": "rm -rf /"},
    ],
    ids=["redirect", "parallelize", "invented-target", "retry-without-target", "wait-with-target", "empty-reason", "extra-field"],
)
def test_invalid_decisions_are_rejected(decision):
    with pytest.raises(ValidationError):
        Decision.model_validate(decision)


def test_valid_proposal_is_executed_as_proposed():
    proposal = ProposedDecision(action="RETRY", target="inventory.primary", reason_code="TRANSIENT_RETRY")

    executed = resolve_action(proposal, ValidationResult.ok())

    assert executed == Decision(action=Action.RETRY, target="inventory.primary", reason_code="TRANSIENT_RETRY")


def test_invalid_proposal_becomes_abort_invalid_decision():
    # Metodologia, Código 9: sem autocorreção, sem fallback para Rules.
    proposal = ProposedDecision(action="RETRY", target="service_c", reason_code="RETRY")

    executed = resolve_action(proposal, ValidationResult.invalid(ValidationErrorCode.UNKNOWN_TARGET))

    assert executed == INVALID_DECISION_ABORT
    assert (executed.action, executed.target, executed.reason_code) == ("ABORT", None, "INVALID_DECISION")


def test_missing_proposal_becomes_abort_invalid_decision():
    assert resolve_action(None, ValidationResult.invalid(ValidationErrorCode.MALFORMED_DECISION)) == INVALID_DECISION_ABORT
