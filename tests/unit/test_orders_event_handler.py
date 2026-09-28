import sqlite3

import pytest

from services.orders.app.db.connection import connect, init_database
from services.orders.app.db.repositories import create_order_with_task, mark_task_dispatched
from services.orders.app.orchestration.event_handler import (
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


def test_success_event_completes_task_and_order(connection):
    changed = handle_inventory_event(connection, _event(EventType.STOCK_RESERVATION_SUCCEEDED))

    assert changed is True
    assert _statuses(connection) == ("COMPLETED", "COMPLETED", "ok")


def test_repeated_success_event_does_not_change_terminal_task(connection):
    handle_inventory_event(connection, _event(EventType.STOCK_RESERVATION_SUCCEEDED))

    changed = handle_inventory_event(connection, _event(EventType.STOCK_RESERVATION_SUCCEEDED))

    assert changed is False
    assert _statuses(connection) == ("COMPLETED", "COMPLETED", "ok")


def test_success_event_for_unknown_task_changes_nothing(connection):
    changed = handle_inventory_event(
        connection, _event(EventType.STOCK_RESERVATION_SUCCEEDED, task_id="TASK_999999")
    )

    assert changed is False
    assert _statuses(connection) == ("DISPATCHED", "PENDING", None)


def test_non_success_events_are_not_handled_before_the_decision_engine(connection):
    with pytest.raises(UnsupportedEventError):
        handle_inventory_event(connection, _event(EventType.STOCK_RESERVATION_FAILED))

    assert _statuses(connection) == ("DISPATCHED", "PENDING", None)
