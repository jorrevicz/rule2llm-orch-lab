import json

import pytest

from services.orders.app.orchestration.rules_engine import RulesDecisionEngine
from services.orders.app.orchestration.timeouts import register_timeout
from shared.decision import Action, Decision, ReasonCode
from tests.factories import Harness

TASK = "TASK_000001"
RETRY = Decision(action=Action.RETRY, target="inventory.primary", reason_code=ReasonCode.TRANSIENT_RETRY)


@pytest.fixture
def harness(tmp_path) -> Harness:
    harness = Harness.with_task(tmp_path)
    harness.dispatch()
    yield harness
    harness.connection.close()


def test_retry_starts_a_new_logical_attempt(harness):
    harness.executor.execute(harness.connection, RETRY, task_id=TASK, decision_id="DEC_R")

    task = harness.task()
    assert (task["status"], task["attempt_number"], task["current_target"]) == ("RETRYING", 2, "inventory.primary")
    assert harness.trajectory()[-1][:2] == (3, "RETRY_SCHEDULED")


def test_retry_dispatch_waits_for_retry_delay(harness):
    harness.scheduler.calls.clear()

    harness.executor.execute(harness.connection, RETRY, task_id=TASK, decision_id="DEC_R")

    assert harness.scheduler.calls == [
        ("dispatch", {"task_id": TASK, "decision_id": "DEC_R", "delay_ms": harness.config.messaging.retry_delay_ms})
    ]
    assert len(harness.publisher.published) == 1  # só o despacho inicial por enquanto


def test_scheduled_attempt_has_new_message_new_seq_same_task_and_target(harness):
    first = harness.publisher.published[0][0]
    harness.executor.execute(harness.connection, RETRY, task_id=TASK, decision_id="DEC_R")

    assert harness.executor.dispatch_scheduled_attempt(harness.connection, task_id=TASK, decision_id="DEC_R")

    retry, route = harness.publisher.published[-1]
    assert route == "inventory.primary"
    assert retry["task_id"] == first["task_id"]
    assert retry["message_id"] != first["message_id"]
    assert retry["event_seq"] > first["event_seq"]
    assert (first["attempt_number"], retry["attempt_number"]) == (1, 2)
    assert retry["decision_id"] == "DEC_R"
    assert harness.task()["status"] == "DISPATCHED"
    assert harness.scheduler.calls[-1][0] == "timeout_check"
    assert harness.scheduler.calls[-1][1]["request_message_id"] == retry["message_id"]


def test_scheduled_attempt_is_dispatched_only_once(harness):
    harness.executor.execute(harness.connection, RETRY, task_id=TASK, decision_id="DEC_R")
    harness.executor.dispatch_scheduled_attempt(harness.connection, task_id=TASK, decision_id="DEC_R")

    assert not harness.executor.dispatch_scheduled_attempt(harness.connection, task_id=TASK, decision_id="DEC_R")
    assert len(harness.publisher.published) == 2


def test_scheduled_attempt_is_skipped_if_the_task_already_finished(harness):
    harness.executor.execute(harness.connection, RETRY, task_id=TASK, decision_id="DEC_R")
    harness.connection.execute("UPDATE tasks SET status = 'COMPLETED'")

    assert not harness.executor.dispatch_scheduled_attempt(harness.connection, task_id=TASK, decision_id="DEC_R")


def test_timeout_leads_to_retry_through_the_orchestrator(harness):
    request = harness.publisher.published[0][0]
    register_timeout(harness.connection, task_id=TASK, request_message_id=request["message_id"])

    outcome = harness.orchestrator.handle_decision_point(harness.connection, TASK)

    assert (outcome.executed.action, outcome.executed.reason_code) == ("RETRY", "TRANSIENT_RETRY")
    decision = json.loads(harness.decisions_path.read_text().splitlines()[-1])
    assert decision["validation"]["valid"] is True
    assert harness.task()["attempt_number"] == 2


def test_attempts_are_bounded_by_max_attempts(harness):
    # 3 tentativas no total (max_attempts = 3): a 3ª não pode gerar outro RETRY.
    for _ in range(harness.config.messaging.max_attempts - 1):
        harness.executor.execute(harness.connection, RETRY, task_id=TASK, decision_id="DEC_R")
        harness.executor.dispatch_scheduled_attempt(harness.connection, task_id=TASK, decision_id="DEC_R")
    request = harness.publisher.published[-1][0]
    register_timeout(harness.connection, task_id=TASK, request_message_id=request["message_id"])

    # Só a decisão: a execução do FALLBACK é coberta nos testes da M4-T08.
    state = harness.orchestrator._state_builder.build(harness.connection, TASK)
    decision = RulesDecisionEngine(harness.config).policy(state)

    assert request["attempt_number"] == 3
    assert (decision.action, decision.reason_code) == ("FALLBACK", "PRIMARY_EXHAUSTED")
