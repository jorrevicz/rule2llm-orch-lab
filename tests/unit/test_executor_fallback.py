import pytest

from services.orders.app.orchestration.event_handler import handle_inventory_event
from services.orders.app.orchestration.timeouts import register_timeout
from shared.decision import Action, Decision, ReasonCode
from shared.envelope import MessageEnvelope, build_envelope
from shared.events import EventType
from tests.factories import Harness

TASK = "TASK_000001"
FALLBACK = Decision(action=Action.FALLBACK, target="inventory.fallback", reason_code=ReasonCode.PRIMARY_EXHAUSTED)


@pytest.fixture
def harness(tmp_path) -> Harness:
    harness = Harness.with_task(tmp_path)
    harness.dispatch()
    yield harness
    harness.connection.close()


def test_fallback_switches_route_without_consuming_an_attempt(harness):
    harness.executor.execute(harness.connection, FALLBACK, task_id=TASK, decision_id="DEC_F")

    task = harness.task()
    assert (task["status"], task["current_target"], task["fallback_used"], task["attempt_number"]) == (
        "FALLBACK_PROCESSING",
        "inventory.fallback",
        1,
        1,  # D-15
    )
    assert [event[1] for event in harness.trajectory()[-2:]] == ["FALLBACK_SCHEDULED", "STOCK_RESERVATION_REQUESTED"]


def test_fallback_request_goes_to_the_fallback_queue_with_new_identifiers(harness):
    first = harness.publisher.published[0][0]

    harness.executor.execute(harness.connection, FALLBACK, task_id=TASK, decision_id="DEC_F")

    request, route = harness.publisher.published[-1]
    assert route == "inventory.fallback"
    assert request["target"] == "inventory.fallback"
    assert request["message_id"] != first["message_id"]
    assert request["event_seq"] > first["event_seq"]
    assert request["attempt_number"] == first["attempt_number"]
    assert harness.scheduler.calls[-1][1]["request_message_id"] == request["message_id"]


def test_fallback_cannot_be_used_twice(harness):
    harness.executor.execute(harness.connection, FALLBACK, task_id=TASK, decision_id="DEC_F")

    with pytest.raises(RuntimeError):
        harness.executor.execute(harness.connection, FALLBACK, task_id=TASK, decision_id="DEC_F2")


def _time_out_latest_request(harness) -> None:
    request = harness.publisher.published[-1][0]
    assert register_timeout(harness.connection, task_id=TASK, request_message_id=request["message_id"])


def _decide(harness) -> tuple[str, str]:
    outcome = harness.orchestrator.handle_decision_point(harness.connection, TASK)
    if outcome.executed.action == "RETRY":
        harness.executor.dispatch_scheduled_attempt(harness.connection, task_id=TASK, decision_id=outcome.decision_id)
    return outcome.executed.action, outcome.executed.reason_code


def test_persistent_timeouts_go_retry_retry_fallback_then_abort(harness):
    decisions = []
    for _ in range(4):
        _time_out_latest_request(harness)
        decisions.append(_decide(harness))

    assert decisions == [
        ("RETRY", "TRANSIENT_RETRY"),
        ("RETRY", "TRANSIENT_RETRY"),
        ("FALLBACK", "PRIMARY_EXHAUSTED"),
        ("ABORT", "FALLBACK_FAILED"),  # timeout no fallback → fallback_failed (D-15)
    ]
    task = harness.task()
    assert (task["status"], task["attempt_number"], task["fallback_used"]) == ("ABORTED", 3, 1)
    assert harness.order_status() == "FAILED"
    assert [route for _, route in harness.publisher.published] == ["inventory.primary"] * 3 + ["inventory.fallback"]


def test_success_on_the_fallback_route_completes_the_task(harness):
    for _ in range(3):
        _time_out_latest_request(harness)
        _decide(harness)
    request = harness.publisher.published[-1][0]
    reply = MessageEnvelope.model_validate(
        build_envelope(
            execution_id="PILOT_TEST",
            task_id=TASK,
            event_type=EventType.STOCK_RESERVATION_SUCCEEDED,
            event_seq=request["event_seq"],
            attempt_number=request["attempt_number"],
            target="inventory.fallback",
            payload={"order_id": "ORD_000001", "route": "fallback"},
        )
    )

    handle_inventory_event(harness.connection, reply)

    assert harness.task()["status"] == "COMPLETED"
    assert harness.order_status() == "COMPLETED"
