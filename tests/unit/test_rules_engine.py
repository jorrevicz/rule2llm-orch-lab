from pathlib import Path

import pytest

from services.orders.app.orchestration.rules_engine import RulesDecisionEngine
from shared.config import load_experiment_config
from tests.factories import dispatched, make_state

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "experiment_config.yml"
CONFIG = load_experiment_config(REPO_CONFIG)
DEADLINE = CONFIG.messaging.task_deadline_ms
WATERMARK = CONFIG.messaging.queue_high_watermark
MAX_ATTEMPTS = CONFIG.messaging.max_attempts
MAX_WAITS = CONFIG.messaging.max_waits


@pytest.fixture
def engine() -> RulesDecisionEngine:
    return RulesDecisionEngine(CONFIG)


def _decide(engine, state) -> tuple:
    decision = engine.policy(state)
    return decision.action, decision.target, decision.reason_code


def test_engine_is_identified_as_rules(engine):
    assert engine.name == "RULES"


def test_output_is_a_proposal_in_the_common_contract(engine):
    output = engine.decide(make_state())

    assert output.proposal.model_dump() == {
        "action": "CONTINUE",
        "target": "inventory.primary",
        "reason_code": "NORMAL_FLOW",
    }
    assert output.llm_inference_ms is None and output.token_usage is None


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        # 1. deadline
        (make_state(**{"task.elapsed_ms": DEADLINE}), ("ABORT", None, "TASK_DEADLINE_EXCEEDED")),
        # 2. dados inválidos
        (dispatched(**{"service.last_result": "invalid_data"}), ("ABORT", None, "INVALID_DATA")),
        # 3. fallback falhou
        (dispatched(**{"service.last_result": "fallback_failed"}), ("ABORT", None, "FALLBACK_FAILED")),
        # 4. serviço indisponível
        (make_state(**{"service.status": "unavailable"}), ("WAIT", None, "SERVICE_UNAVAILABLE")),
        (
            make_state(**{"service.status": "unavailable", "task.wait_count": MAX_WAITS}),
            ("ABORT", None, "SERVICE_UNAVAILABLE_LIMIT"),
        ),
        # 5. pressão de fila
        (make_state(**{"messaging.queue_size": WATERMARK}), ("WAIT", None, "QUEUE_PRESSURE")),
        (
            make_state(**{"messaging.queue_size": WATERMARK, "task.wait_count": MAX_WAITS}),
            ("ABORT", None, "QUEUE_PRESSURE_LIMIT"),
        ),
        # 6. timeout / falha transitória
        (dispatched(**{"service.last_result": "timeout"}), ("RETRY", "inventory.primary", "TRANSIENT_RETRY")),
        (
            dispatched(**{"service.last_result": "transient_error"}),
            ("RETRY", "inventory.primary", "TRANSIENT_RETRY"),
        ),
        (
            dispatched(**{"service.last_result": "timeout", "task.attempt_number": MAX_ATTEMPTS}),
            ("FALLBACK", "inventory.fallback", "PRIMARY_EXHAUSTED"),
        ),
        (
            dispatched(
                **{
                    "service.last_result": "timeout",
                    "task.attempt_number": MAX_ATTEMPTS,
                    "alternatives.fallback_available": False,
                    "alternatives.alternative_targets": [],
                }
            ),
            ("ABORT", None, "ATTEMPTS_EXHAUSTED"),
        ),
        (
            dispatched(
                **{
                    "service.last_result": "timeout",
                    "task.attempt_number": MAX_ATTEMPTS,
                    "alternatives.fallback_used": True,
                    "alternatives.alternative_targets": [],
                }
            ),
            ("ABORT", None, "ATTEMPTS_EXHAUSTED"),
        ),
        # 7. fluxo normal (D-06)
        (make_state(), ("CONTINUE", "inventory.primary", "NORMAL_FLOW")),
        (make_state(**{"task.phase": "WAITING", "task.wait_count": 1}), ("CONTINUE", "inventory.primary", "NORMAL_FLOW")),
        # 8. não mapeado
        (dispatched(), ("ABORT", None, "UNMAPPED_STATE")),
        (dispatched(**{"task.phase": "COMPLETED"}), ("ABORT", None, "UNMAPPED_STATE")),
    ],
    ids=[
        "deadline",
        "invalid-data",
        "fallback-failed",
        "unavailable-wait",
        "unavailable-limit",
        "queue-pressure-wait",
        "queue-pressure-limit",
        "timeout-retry",
        "transient-retry",
        "attempts-exhausted-fallback",
        "attempts-exhausted-no-fallback",
        "attempts-exhausted-fallback-used",
        "pending-continue",
        "waiting-continue",
        "dispatched-without-result",
        "terminal",
    ],
)
def test_ordered_policy(engine, state, expected):
    assert _decide(engine, state) == expected


def test_deadline_has_priority_over_everything(engine):
    state = dispatched(**{"task.elapsed_ms": DEADLINE, "service.last_result": "timeout", "service.status": "unavailable"})

    assert _decide(engine, state) == ("ABORT", None, "TASK_DEADLINE_EXCEEDED")


def test_unavailable_service_has_priority_over_fallback(engine):
    # Inventory inteiro fora do ar: o fallback (mesmo serviço) não resolve (docs/06 §6.5).
    state = dispatched(
        **{"service.status": "unavailable", "service.last_result": "timeout", "task.attempt_number": MAX_ATTEMPTS}
    )

    assert _decide(engine, state)[0] == "WAIT"


def test_policy_is_deterministic(engine):
    state = dispatched(**{"service.last_result": "timeout"})

    assert len({_decide(engine, state) for _ in range(50)}) == 1
