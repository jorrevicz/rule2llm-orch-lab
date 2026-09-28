import sqlite3

import pytest

from services.inventory.app.db.connection import connect, init_database
from services.inventory.app.reservation.service import process_reservation_request
from shared.envelope import build_envelope, parse_message
from shared.events import EventType


class RecordingPublisher:
    def __init__(self) -> None:
        self.published: list[dict] = []

    def publish(self, envelope: dict) -> None:
        self.published.append(envelope)


@pytest.fixture
def connection(tmp_path) -> sqlite3.Connection:
    path = tmp_path / "inventory.db"
    init_database(path)
    conn = connect(path)
    yield conn
    conn.close()


def _request(target: str = "inventory.primary"):
    envelope = build_envelope(
        execution_id="PILOT_TEST",
        task_id="TASK_000001",
        event_type=EventType.STOCK_RESERVATION_REQUESTED,
        event_seq=1,
        attempt_number=1,
        target=target,
        payload={"order_id": "ORD_000001", "items": [{"sku": "SKU-001", "quantity": 2}]},
    )
    return parse_message(envelope, {EventType.STOCK_RESERVATION_REQUESTED})


def test_primary_reservation_is_persisted(connection):
    request, payload = _request()

    process_reservation_request(connection, request, payload, RecordingPublisher())

    reservation = connection.execute("SELECT * FROM reservations").fetchone()
    assert reservation["task_id"] == "TASK_000001"
    assert reservation["order_id"] == "ORD_000001"
    assert reservation["status"] == "RESERVED"
    assert reservation["route"] == "primary"
    assert reservation["items_json"] == '[{"quantity":2,"sku":"SKU-001"}]'
    processed = connection.execute("SELECT * FROM processed_messages").fetchone()
    assert processed["message_id"] == request.message_id


def test_success_event_is_published_after_persisting(connection):
    request, payload = _request()
    publisher = RecordingPublisher()

    process_reservation_request(connection, request, payload, publisher)

    [event] = publisher.published
    assert event["event_type"] == "STOCK_RESERVATION_SUCCEEDED"
    assert event["task_id"] == request.task_id
    assert event["execution_id"] == request.execution_id
    assert event["attempt_number"] == request.attempt_number
    assert event["message_id"] != request.message_id
    assert event["event_seq"] == request.event_seq  # D-16: correlação com a solicitação
    assert event["payload"] == {"order_id": "ORD_000001", "route": "primary"}


def test_nothing_is_published_when_persistence_fails(connection):
    publisher = RecordingPublisher()
    connection.execute("DROP TABLE processed_messages")

    with pytest.raises(sqlite3.OperationalError):
        process_reservation_request(connection, *_request(), publisher)

    assert publisher.published == []
    assert connection.execute("SELECT COUNT(*) FROM reservations").fetchone()[0] == 0


def test_fallback_route_is_not_handled_yet(connection):
    with pytest.raises(ValueError, match="unsupported target"):
        process_reservation_request(connection, *_request("inventory.fallback"), RecordingPublisher())
