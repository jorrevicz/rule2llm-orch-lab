import json

import pytest

from services.orders.app.observability.recorders import DecisionRecord
from services.orders.app.orchestration.decision_engine import EngineOutput
from services.orders.app.orchestration.orchestrator import Orchestrator
from shared.decision import ProposedDecision
from tests.factories import Harness

TASK = "TASK_000001"


@pytest.fixture
def harness(tmp_path) -> Harness:
    harness = Harness.with_task(tmp_path)
    yield harness
    harness.connection.close()


def _jsonl(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_initial_decision_point_dispatches_to_primary(harness):
    outcome = harness.orchestrator.handle_decision_point(harness.connection, TASK)

    assert (outcome.executed.action, outcome.executed.target, outcome.executed.reason_code) == (
        "CONTINUE",
        "inventory.primary",
        "NORMAL_FLOW",
    )
    task = harness.task()
    assert (task["status"], task["current_target"], task["attempt_number"]) == ("DISPATCHED", "inventory.primary", 1)
    assert harness.trajectory() == [
        (1, "TASK_CREATED", 1, None, 0),
        (2, "STOCK_RESERVATION_REQUESTED", 1, "inventory.primary", 0),
    ]


def test_request_carries_the_decision_and_timeout_is_scheduled(harness):
    outcome = harness.orchestrator.handle_decision_point(harness.connection, TASK)

    [(envelope, route)] = harness.publisher.published
    assert route == "inventory.primary"
    assert envelope["decision_id"] == outcome.decision_id
    assert envelope["event_seq"] == 2 and envelope["attempt_number"] == 1
    assert harness.scheduler.calls == [
        (
            "timeout_check",
            {
                "task_id": TASK,
                "request_message_id": envelope["message_id"],
                "delay_ms": harness.config.messaging.inventory_timeout_ms,
            },
        )
    ]


def test_decision_is_recorded_and_points_to_its_state(harness):
    outcome = harness.orchestrator.handle_decision_point(harness.connection, TASK)

    [state] = _jsonl(harness.states_path)
    [decision] = _jsonl(harness.decisions_path)
    DecisionRecord.model_validate(decision)
    assert decision["decision_id"] == outcome.decision_id
    assert decision["state_id"] == state["state_id"] == outcome.state_id
    assert decision["decision_engine"] == "RULES"
    assert decision["proposed_decision"] == decision["executed_decision"]
    assert decision["validation"] == {"valid": True, "error": None}
    assert decision["decision_time_ms"] >= 0
    assert decision["llm_inference_ms"] is None and decision["token_usage"] is None


class FixedEngine:
    """Motor de teste que sempre propõe a mesma coisa."""

    name = "LLM"

    def __init__(self, proposal: ProposedDecision | None) -> None:
        self.proposal = proposal

    def decide(self, state) -> EngineOutput:
        return EngineOutput(proposal=self.proposal, llm_inference_ms=12.5, token_usage=None)


def _with_engine(harness: Harness, engine) -> Orchestrator:
    return Orchestrator(
        state_builder=harness.orchestrator._state_builder,
        engine=engine,
        validator=harness.orchestrator._validator,
        executor=harness.executor,
        state_recorder=harness.orchestrator._state_recorder,
        decision_recorder=harness.orchestrator._decision_recorder,
    )


def test_invalid_proposal_is_recorded_and_aborted_without_correction(harness):
    proposal = ProposedDecision(action="RETRY", target="service_c", reason_code="RETRY")
    orchestrator = _with_engine(harness, FixedEngine(proposal))

    outcome = orchestrator.handle_decision_point(harness.connection, TASK)

    [decision] = _jsonl(harness.decisions_path)
    assert decision["proposed_decision"] == {"action": "RETRY", "target": "service_c", "reason_code": "RETRY"}
    assert decision["validation"] == {"valid": False, "error": "UNKNOWN_TARGET"}
    assert decision["executed_decision"] == {"action": "ABORT", "target": None, "reason_code": "INVALID_DECISION"}
    assert outcome.execution.changed_state
    assert harness.task()["status"] == "ABORTED"
    assert harness.publisher.published == []


def test_unreadable_output_is_recorded_as_malformed(harness):
    orchestrator = _with_engine(harness, FixedEngine(None))

    orchestrator.handle_decision_point(harness.connection, TASK)

    [decision] = _jsonl(harness.decisions_path)
    assert decision["proposed_decision"] is None
    assert decision["validation"] == {"valid": False, "error": "MALFORMED_DECISION"}
    assert decision["executed_decision"]["reason_code"] == "INVALID_DECISION"


def test_continue_without_target_dispatches_to_the_primary_route(harness):
    # D-18: o destino do CONTINUE é o do fluxo; o LLM real responde target null.
    orchestrator = _with_engine(harness, FixedEngine(ProposedDecision(action="CONTINUE", target=None, reason_code="000")))

    outcome = orchestrator.handle_decision_point(harness.connection, TASK)

    assert outcome.validation.valid
    assert harness.task()["current_target"] == "inventory.primary"
    assert harness.publisher.published[-1][1] == "inventory.primary"
