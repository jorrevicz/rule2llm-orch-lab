import pytest

from services.orders.app.orchestration.broker_observer import QueueStats
from services.orders.app.orchestration.waits import finish_wait
from shared.decision import Action, Decision, ReasonCode
from tests.factories import Harness

TASK = "TASK_000001"
WAIT = Decision(action=Action.WAIT, target=None, reason_code=ReasonCode.QUEUE_PRESSURE)
DOWN = QueueStats(message_count=0, consumer_count=0)
UP = QueueStats(message_count=0, consumer_count=1)


@pytest.fixture
def harness(tmp_path) -> Harness:
    harness = Harness.with_task(tmp_path)
    yield harness
    harness.connection.close()


def test_wait_does_not_consume_an_attempt(harness):
    harness.executor.execute(harness.connection, WAIT, task_id=TASK, decision_id="DEC_W")

    task = harness.task()
    assert (task["status"], task["wait_count"], task["attempt_number"]) == ("WAITING", 1, 1)
    assert harness.trajectory()[-1][:2] == (2, "WAIT_SCHEDULED")
    assert harness.publisher.published == []


def test_wait_schedules_a_reevaluation(harness):
    harness.executor.execute(harness.connection, WAIT, task_id=TASK, decision_id="DEC_W")

    assert harness.scheduler.calls == [
        ("reevaluate", {"task_id": TASK, "wait_count": 1, "delay_ms": harness.config.messaging.wait_delay_ms})
    ]


def test_finished_wait_is_recorded_once(harness):
    harness.executor.execute(harness.connection, WAIT, task_id=TASK, decision_id="DEC_W")

    assert finish_wait(harness.connection, task_id=TASK, wait_count=1)
    assert not finish_wait(harness.connection, task_id=TASK, wait_count=1)
    assert [event[1] for event in harness.trajectory()].count("WAIT_FINISHED") == 1


def test_stale_reevaluation_is_ignored(harness):
    harness.executor.execute(harness.connection, WAIT, task_id=TASK, decision_id="DEC_W")
    harness.executor.execute(harness.connection, WAIT, task_id=TASK, decision_id="DEC_W2")

    assert not finish_wait(harness.connection, task_id=TASK, wait_count=1)


def test_task_finished_during_the_wait_is_not_reevaluated(harness):
    harness.executor.execute(harness.connection, WAIT, task_id=TASK, decision_id="DEC_W")
    harness.connection.execute("UPDATE tasks SET status = 'COMPLETED'")

    assert not finish_wait(harness.connection, task_id=TASK, wait_count=1)


def _decide(harness) -> tuple[str, str]:
    outcome = harness.orchestrator.handle_decision_point(harness.connection, TASK)
    return outcome.executed.action, outcome.executed.reason_code


def test_unavailable_service_waits_until_the_limit_then_aborts(harness):
    harness.observer.stats["inventory.primary"] = DOWN
    harness.observer.stats["inventory.fallback"] = DOWN
    max_waits = harness.config.messaging.max_waits

    decisions = [_decide(harness)]
    for wait_count in range(1, max_waits + 1):
        assert finish_wait(harness.connection, task_id=TASK, wait_count=wait_count)
        decisions.append(_decide(harness))

    assert decisions == [("WAIT", "SERVICE_UNAVAILABLE")] * max_waits + [("ABORT", "SERVICE_UNAVAILABLE_LIMIT")]
    task = harness.task()
    assert (task["status"], task["wait_count"], task["attempt_number"]) == ("ABORTED", max_waits, 1)
    assert harness.order_status() == "FAILED"


def test_service_recovered_during_the_wait_continues_the_flow(harness):
    harness.observer.stats["inventory.primary"] = DOWN
    assert _decide(harness) == ("WAIT", "SERVICE_UNAVAILABLE")

    harness.observer.stats["inventory.primary"] = UP
    finish_wait(harness.connection, task_id=TASK, wait_count=1)

    assert _decide(harness) == ("CONTINUE", "NORMAL_FLOW")  # D-06: WAITING → CONTINUE
    assert harness.task()["status"] == "DISPATCHED"
    assert [event[1] for event in harness.trajectory()] == [
        "TASK_CREATED",
        "WAIT_SCHEDULED",
        "WAIT_FINISHED",
        "STOCK_RESERVATION_REQUESTED",
    ]
