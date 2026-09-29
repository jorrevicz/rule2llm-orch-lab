from pathlib import Path

import pytest

from services.orders.app.orchestration.rules_engine import RulesDecisionEngine
from services.orders.app.orchestration.validator import DecisionValidator
from shared.config import load_experiment_config
from shared.decision import ProposedDecision
from tests.factories import dispatched, make_state

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "experiment_config.yml"
CONFIG = load_experiment_config(REPO_CONFIG)

ON_FALLBACK = {
    "task.phase": "FALLBACK_PROCESSING",
    "task.current_target": "inventory.fallback",
    "alternatives.fallback_used": True,
    "alternatives.alternative_targets": [],
}


@pytest.fixture
def validator() -> DecisionValidator:
    return DecisionValidator(CONFIG)


def _proposal(action, target, reason="REASON") -> ProposedDecision:
    return ProposedDecision(action=action, target=target, reason_code=reason)


def _error(validator, proposal, state):
    result = validator.validate(proposal, state)
    return None if result.valid else result.error


@pytest.mark.parametrize(
    ("proposal", "state"),
    [
        (_proposal("CONTINUE", "inventory.primary"), make_state()),
        (_proposal("CONTINUE", "inventory.primary"), make_state(**{"task.phase": "WAITING"})),
        (_proposal("CONTINUE", None), make_state()),  # D-18
        (_proposal("RETRY", "inventory.primary"), dispatched(**{"service.last_result": "timeout"})),
        (_proposal("FALLBACK", "inventory.fallback"), dispatched()),
        (_proposal("FALLBACK", "inventory.fallback"), make_state()),
        (_proposal("WAIT", None), dispatched()),
        (_proposal("ABORT", None), dispatched()),
        (_proposal("ABORT", None), make_state(**ON_FALLBACK)),
    ],
    ids=["continue-pending", "continue-waiting", "continue-null-target", "retry", "fallback", "fallback-before-dispatch", "wait", "abort", "abort-on-fallback"],
)
def test_valid_decisions(validator, proposal, state):
    assert validator.validate(proposal, state).valid


@pytest.mark.parametrize(
    ("proposal", "state", "error"),
    [
        (None, make_state(), "MALFORMED_DECISION"),
        (_proposal("REDIRECT", "inventory.primary"), dispatched(), "UNKNOWN_ACTION"),
        (_proposal("PARALLELIZE", None), dispatched(), "UNKNOWN_ACTION"),
        (_proposal(None, None), dispatched(), "UNKNOWN_ACTION"),
        (_proposal("RETRY", "service_c", "RETRY"), dispatched(), "UNKNOWN_TARGET"),  # Código 9
        (_proposal("ABORT", None, ""), dispatched(), "MISSING_REASON_CODE"),
        (_proposal("ABORT", None, None), dispatched(), "MISSING_REASON_CODE"),
        (_proposal("CONTINUE", "inventory.fallback"), make_state(), "INVALID_CONTINUE_TARGET"),
        (_proposal("CONTINUE", "inventory.primary"), dispatched(), "CONTINUE_AFTER_DISPATCH"),
        (
            _proposal("RETRY", "inventory.primary"),
            dispatched(**{"task.attempt_number": 3}),
            "RETRY_LIMIT_EXCEEDED",
        ),
        (_proposal("RETRY", "inventory.fallback"), dispatched(), "INVALID_RETRY_TARGET"),
        (_proposal("RETRY", "inventory.primary"), make_state(), "INVALID_RETRY_TARGET"),
        (_proposal("RETRY", "inventory.fallback"), make_state(**ON_FALLBACK), "FALLBACK_RETRY_LIMIT"),
        (
            _proposal("FALLBACK", "inventory.fallback"),
            dispatched(**{"alternatives.fallback_available": False, "alternatives.alternative_targets": []}),
            "FALLBACK_NOT_AVAILABLE",
        ),
        (_proposal("FALLBACK", "inventory.fallback"), make_state(**ON_FALLBACK), "FALLBACK_ALREADY_USED"),
        (_proposal("FALLBACK", "inventory.primary"), dispatched(), "INVALID_FALLBACK_TARGET"),
        (_proposal("WAIT", "inventory.primary"), dispatched(), "TARGET_NOT_ALLOWED"),
        (_proposal("ABORT", "inventory.primary"), dispatched(), "TARGET_NOT_ALLOWED"),
        (_proposal("ABORT", None), dispatched(**{"task.phase": "COMPLETED"}), "TERMINAL_TASK"),
        (_proposal("WAIT", None), dispatched(**{"task.phase": "ABORTED"}), "TERMINAL_TASK"),
        (_proposal("WAIT", None), dispatched(**{"task.phase": "DEAD_LETTERED"}), "TERMINAL_TASK"),
    ],
    ids=[
        "malformed",
        "redirect",
        "parallelize",
        "missing-action",
        "invented-target",
        "empty-reason",
        "missing-reason",
        "continue-to-fallback",
        "continue-after-dispatch",
        "retry-limit",
        "retry-other-target",
        "retry-before-dispatch",
        "retry-on-fallback",
        "fallback-unavailable",
        "fallback-used",
        "fallback-wrong-target",
        "wait-with-target",
        "abort-with-target",
        "terminal-completed",
        "terminal-aborted",
        "terminal-dead-lettered",
    ],
)
def test_invalid_decisions(validator, proposal, state, error):
    assert _error(validator, proposal, state) == error


def test_every_rules_decision_is_valid(validator):
    # O validador é o mesmo para Rules e LLM; a política Rules nunca propõe decisão inválida
    # nos estados não terminais.
    engine = RulesDecisionEngine(CONFIG)
    states = [
        make_state(),
        make_state(**{"task.phase": "WAITING", "task.wait_count": 1}),
        make_state(**{"service.status": "unavailable"}),
        make_state(**{"messaging.queue_size": 999}),
        dispatched(**{"service.last_result": "timeout"}),
        dispatched(**{"service.last_result": "timeout", "task.attempt_number": 3}),
        dispatched(**{"service.last_result": "invalid_data"}),
        make_state(**{**ON_FALLBACK, "service.last_result": "fallback_failed"}),
        dispatched(),
    ]
    for state in states:
        assert validator.validate(engine.decide(state).proposal, state).valid, state


def test_only_single_fallback_attempt_is_supported():
    config = CONFIG.model_copy(
        update={"messaging": CONFIG.messaging.model_copy(update={"fallback_max_attempts": 2})}
    )

    with pytest.raises(ValueError, match="D-15"):
        DecisionValidator(config)
