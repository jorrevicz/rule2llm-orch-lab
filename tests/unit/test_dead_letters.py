import json

import pytest

from services.orders.app.orchestration.dead_letters import DeadLetter, handle_dead_letter
from services.orders.app.messaging.celery_app import app as orders_app
from services.inventory.app.messaging.celery_app import app as inventory_app
from tests.factories import Harness

TASK = "TASK_000001"
EMBED = {"callbacks": None, "errbacks": None, "chain": None, "chord": None}


def _headers(task: str, queue: str, reason: str = "rejected") -> dict:
    return {"task": task, "x-death": [{"queue": queue, "reason": reason, "count": 1}]}


@pytest.fixture
def harness(tmp_path) -> Harness:
    harness = Harness.with_task(tmp_path)
    harness.dispatch()
    yield harness
    harness.connection.close()


def test_unexpected_failures_are_dead_lettered_not_discarded():
    # D-07: sem ack em falha → rejeição sem requeue → tasks.dlq.
    for app in (orders_app, inventory_app):
        assert app.conf.task_acks_on_failure_or_timeout is False


def test_dead_letter_is_read_from_a_celery_message():
    request = {"task_id": TASK, "message_id": "MSG_1", "event_type": "STOCK_RESERVATION_REQUESTED"}

    dead = DeadLetter.from_celery_message(
        [[request], {}, EMBED], _headers("inventory.reserve_stock", "inventory.primary")
    )

    assert dead == DeadLetter(
        task_name="inventory.reserve_stock",
        task_id=TASK,
        message_id="MSG_1",
        event_type="STOCK_RESERVATION_REQUESTED",
        origin_queue="inventory.primary",
        reason="rejected",
    )


def test_internal_task_is_identified_by_its_kwargs():
    dead = DeadLetter.from_celery_message(
        [[], {"task_id": TASK, "request_message_id": "MSG_1"}, EMBED],
        _headers("orders.timeout_check", "orders.events"),
    )

    assert dead.task_id == TASK and dead.message_id is None


@pytest.mark.parametrize("body", ["garbage", [["not a dict"], {}, EMBED], None, [[{"task_id": 42}], {}, EMBED]])
def test_unidentifiable_messages_have_no_task(body):
    assert DeadLetter.from_celery_message(body, {}).task_id is None


def test_dead_lettered_message_ends_an_open_task(harness):
    dead = DeadLetter.from_celery_message(
        [[{"task_id": TASK, "message_id": "MSG_1"}], {}, EMBED],
        _headers("inventory.reserve_stock", "inventory.primary"),
    )

    assert handle_dead_letter(harness.connection, dead)

    assert harness.task()["status"] == "DEAD_LETTERED"
    assert harness.order_status() == "FAILED"
    event = harness.connection.execute("SELECT * FROM task_events ORDER BY event_id DESC").fetchone()
    assert event["event_type"] == "MESSAGE_DEAD_LETTERED"
    assert json.loads(event["payload_json"]) == {
        "task_name": "inventory.reserve_stock",
        "message_id": "MSG_1",
        "event_type": None,
        "origin_queue": "inventory.primary",
        "reason": "rejected",
        "task_state_changed": True,
    }


def test_dead_letter_of_a_finished_task_is_only_recorded(harness):
    harness.connection.execute("UPDATE tasks SET status = 'COMPLETED'")
    dead = DeadLetter.from_celery_message(
        [[{"task_id": TASK}], {}, EMBED], _headers("inventory.reserve_stock", "inventory.primary")
    )

    assert not handle_dead_letter(harness.connection, dead)
    assert harness.task()["status"] == "COMPLETED"
    assert harness.trajectory()[-1][1] == "MESSAGE_DEAD_LETTERED"


def test_dead_letter_of_an_unknown_task_changes_nothing(harness):
    dead = DeadLetter.from_celery_message([[{"task_id": "TASK_999999"}], {}, EMBED], {})

    assert not handle_dead_letter(harness.connection, dead)
    assert harness.trajectory()[-1][1] == "STOCK_RESERVATION_REQUESTED"
