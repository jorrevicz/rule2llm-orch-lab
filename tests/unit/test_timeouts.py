import json

import pytest

from services.orders.app.orchestration.event_handler import handle_inventory_event
from services.orders.app.orchestration.timeouts import register_timeout
from shared.envelope import MessageEnvelope, build_envelope
from shared.events import EventType
from tests.factories import Harness

TASK = "TASK_000001"


@pytest.fixture
def harness(tmp_path) -> Harness:
    harness = Harness.with_task(tmp_path)
    yield harness
    harness.connection.close()


def _reply(request: dict) -> MessageEnvelope:
    return MessageEnvelope.model_validate(
        build_envelope(
            execution_id="PILOT_TEST",
            task_id=TASK,
            event_type=EventType.STOCK_RESERVATION_SUCCEEDED,
            event_seq=request["event_seq"],
            attempt_number=request["attempt_number"],
            target=request["target"],
            payload={"order_id": "ORD_000001", "route": "primary"},
        )
    )


def test_unanswered_request_times_out(harness):
    request = harness.dispatch()

    assert register_timeout(harness.connection, task_id=TASK, request_message_id=request["message_id"])

    assert harness.task()["last_result"] == "timeout"
    event_seq, event_type, *_ = harness.trajectory()[-1]
    assert (event_seq, event_type) == (3, "INVENTORY_TIMEOUT")
    payload = json.loads(
        harness.connection.execute("SELECT payload_json FROM task_events ORDER BY event_id DESC").fetchone()[0]
    )
    assert payload == {"request_message_id": request["message_id"], "last_result": "timeout"}


def test_answered_request_does_not_time_out(harness):
    request = harness.dispatch()
    handle_inventory_event(harness.connection, _reply(request))

    assert not register_timeout(harness.connection, task_id=TASK, request_message_id=request["message_id"])
    assert harness.task()["last_result"] == "ok"


def test_superseded_request_does_not_time_out(harness):
    harness.dispatch()

    assert not register_timeout(harness.connection, task_id=TASK, request_message_id="MSG_older")
    assert harness.task()["last_result"] is None


def test_terminal_task_does_not_time_out(harness):
    request = harness.dispatch()
    harness.connection.execute("UPDATE tasks SET status = 'ABORTED'")

    assert not register_timeout(harness.connection, task_id=TASK, request_message_id=request["message_id"])


def test_timeout_on_the_fallback_route_is_fallback_failed(harness):
    request = harness.dispatch()
    harness.connection.execute("UPDATE tasks SET current_target = 'inventory.fallback'")

    register_timeout(harness.connection, task_id=TASK, request_message_id=request["message_id"])

    assert harness.task()["last_result"] == "fallback_failed"  # D-15


def test_timeout_is_registered_only_once(harness):
    request = harness.dispatch()
    register_timeout(harness.connection, task_id=TASK, request_message_id=request["message_id"])

    # Um segundo disparo do mesmo agendamento (ex.: redelivery) não duplica o timeout
    # nem abre outro ponto de decisão.
    assert not register_timeout(harness.connection, task_id=TASK, request_message_id=request["message_id"])
    assert [row[1] for row in harness.trajectory()].count("INVENTORY_TIMEOUT") == 1
