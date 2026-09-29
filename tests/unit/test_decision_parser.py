import pytest

from services.orders.app.llm.decision_parser import ParseError, parse_decision
from services.orders.app.orchestration.validator import DecisionValidator
from tests.factories import dispatched, make_state, repo_config


def test_contract_decision_is_read_as_proposed():
    result = parse_decision('{"action": "RETRY", "target": "inventory.primary", "reason_code": "TRANSIENT_RETRY"}')

    assert result.error is None
    assert result.proposal.model_dump() == {
        "action": "RETRY",
        "target": "inventory.primary",
        "reason_code": "TRANSIENT_RETRY",
    }


def test_json_null_target_is_null():
    assert parse_decision('{"action": "WAIT", "target": null, "reason_code": "X"}').proposal.target is None


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("", ParseError.INVALID_JSON),
        ("RETRY", ParseError.INVALID_JSON),
        ('Claro! {"action": "RETRY"}', ParseError.INVALID_JSON),
        ('```json\n{"action": "RETRY"}\n```', ParseError.INVALID_JSON),
        ('{"action": "RETRY", "target": "inventory.primary"', ParseError.INVALID_JSON),  # cortado
        ('["RETRY"]', ParseError.NOT_AN_OBJECT),
        ('"RETRY"', ParseError.NOT_AN_OBJECT),
        ('{"action": "RETRY", "target": null, "reason_code": "X", "explanation": "..."}', ParseError.UNEXPECTED_FIELDS),
        ('{"action": "RETRY", "command": "rm -rf /"}', ParseError.UNEXPECTED_FIELDS),
        ('{"action": 3, "target": null, "reason_code": "X"}', ParseError.INVALID_FIELD_TYPE),
        ('{"action": ["RETRY"], "target": null, "reason_code": "X"}', ParseError.INVALID_FIELD_TYPE),
    ],
    ids=[
        "empty",
        "plain-text",
        "text-around-json",
        "markdown-fence",
        "truncated",
        "array",
        "string",
        "extra-explanation",
        "injected-command",
        "numeric-action",
        "list-action",
    ],
)
def test_malformed_output_is_not_a_proposal(text, error):
    result = parse_decision(text)

    assert result.proposal is None
    assert result.error == error


@pytest.mark.parametrize(
    ("text", "state", "validation_error"),
    [
        # Sem correção: o parser entrega como veio e o Validator comum julga.
        ('{"action": "retry", "target": "inventory.primary", "reason_code": "X"}', dispatched(), "UNKNOWN_ACTION"),
        ('{"action": " WAIT", "target": null, "reason_code": "X"}', dispatched(), "UNKNOWN_ACTION"),
        ('{"action": "WAIT", "target": "null", "reason_code": "X"}', dispatched(), "UNKNOWN_TARGET"),
        ('{"target": null, "reason_code": "X"}', dispatched(), "UNKNOWN_ACTION"),
        ('{"action": "ABORT", "target": null}', dispatched(), "MISSING_REASON_CODE"),
        ('{"action": "CONTINUE", "reason_code": "X"}', make_state(), "INVALID_CONTINUE_TARGET"),
    ],
    ids=["lowercase-action", "padded-action", "string-null-target", "missing-action", "missing-reason", "missing-target"],
)
def test_content_errors_are_left_to_the_common_validator(text, state, validation_error):
    proposal = parse_decision(text).proposal

    result = DecisionValidator(repo_config()).validate(proposal, state)

    assert proposal is not None
    assert (result.valid, result.error) == (False, validation_error)
