import sqlite3

import pytest

from services.orders.app.db.connection import connect, init_database
from services.orders.app.db.repositories import create_order_with_task, mark_task_dispatched
from services.orders.app.orchestration.event_handler import (
    EventStatus,
    UnsupportedEventError,
    handle_inventory_event,
)
from shared.envelope import MessageEnvelope, build_envelope
from shared.events import EventType

NOW = "2026-09-27T12:00:00.000Z"


@pytest.fixture
def connection(tmp_path) -> sqlite3.Connection:
    path = tmp_path / "orders.db"
    init_database(path)
    conn = connect(path)
    created = create_order_with_task(conn, execution_id="PILOT_TEST", items_json="[]", now=NOW)
    mark_task_dispatched(conn, task_id=created.task_id, target="inventory.primary", now=NOW)
    yield conn
    conn.close()


def _event(event_type: EventType, task_id: str = "TASK_000001") -> MessageEnvelope:
    envelope = build_envelope(
        execution_id="PILOT_TEST",
        task_id=task_id,
        event_type=event_type,
        event_seq=2,
        attempt_number=1,
        target="inventory.primary",
        payload={"order_id": "ORD_000001", "route": "primary"},
    )
    return MessageEnvelope.model_validate(envelope)


def _statuses(connection: sqlite3.Connection) -> tuple[str, str, str | None]:
    row = connection.execute(
        "SELECT t.status AS task, o.status AS ord, t.last_result AS result"
        " FROM tasks t JOIN orders o ON o.order_id = t.order_id"
    ).fetchone()
    return row["task"], row["ord"], row["result"]


def _event_seq(connection: sqlite3.Connection) -> int:
    return connection.execute("SELECT current_event_seq FROM tasks").fetchone()[0]


def test_success_event_completes_task_and_order(connection):
    outcome = handle_inventory_event(connection, _event(EventType.STOCK_RESERVATION_SUCCEEDED))

    assert outcome.changed_state is True
    assert _statuses(connection) == ("COMPLETED", "COMPLETED", "ok")


def test_trajectory_is_numbered_by_orders(connection):
    # TASK_CREATED = 1, STOCK_RESERVATION_REQUESTED = 2 (fixture), evento recebido = 3.
    assert _event_seq(connection) == 2

    outcome = handle_inventory_event(connection, _event(EventType.STOCK_RESERVATION_SUCCEEDED))

    assert outcome.status == EventStatus.RECORDED
    assert outcome.event_seq == 3
    assert _event_seq(connection) == 3


def test_late_event_for_terminal_task_is_recorded_without_state_change(connection):
    handle_inventory_event(connection, _event(EventType.STOCK_RESERVATION_SUCCEEDED))

    outcome = handle_inventory_event(connection, _event(EventType.STOCK_RESERVATION_SUCCEEDED))

    assert outcome.status == EventStatus.RECORDED
    assert outcome.event_seq == 4
    assert outcome.changed_state is False
    assert _statuses(connection) == ("COMPLETED", "COMPLETED", "ok")


def test_event_for_unknown_task_is_not_recorded(connection):
    outcome = handle_inventory_event(
        connection, _event(EventType.STOCK_RESERVATION_SUCCEEDED, task_id="TASK_999999")
    )

    assert outcome.status == EventStatus.UNKNOWN_TASK
    assert _event_seq(connection) == 2
    assert _statuses(connection) == ("DISPATCHED", "PENDING", None)


def test_non_success_events_are_not_handled_before_the_decision_engine(connection):
    with pytest.raises(UnsupportedEventError):
        handle_inventory_event(connection, _event(EventType.STOCK_RESERVATION_FAILED))

    assert _event_seq(connection) == 2
    assert _statuses(connection) == ("DISPATCHED", "PENDING", None)


def _processed_events(connection: sqlite3.Connection) -> list[tuple[str, str]]:
    return [
        (row["message_id"], row["event_type"])
        for row in connection.execute("SELECT message_id, event_type FROM processed_events")
    ]


def test_new_event_is_recorded_in_processed_events(connection):
    event = _event(EventType.STOCK_RESERVATION_SUCCEEDED)

    handle_inventory_event(connection, event)

    assert _processed_events(connection) == [(event.message_id, "STOCK_RESERVATION_SUCCEEDED")]


def test_repeated_message_is_ignored_without_consuming_event_seq(connection):
    event = _event(EventType.STOCK_RESERVATION_SUCCEEDED)
    handle_inventory_event(connection, event)

    outcome = handle_inventory_event(connection, event)

    assert outcome.status == EventStatus.DUPLICATE
    assert outcome.event_seq is None
    assert _event_seq(connection) == 3
    assert len(_processed_events(connection)) == 1
    assert _statuses(connection) == ("COMPLETED", "COMPLETED", "ok")


def test_event_for_unknown_task_is_not_marked_as_processed(connection):
    handle_inventory_event(
        connection, _event(EventType.STOCK_RESERVATION_SUCCEEDED, task_id="TASK_999999")
    )

    assert _processed_events(connection) == []
