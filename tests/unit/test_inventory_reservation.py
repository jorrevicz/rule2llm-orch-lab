import sqlite3

import pytest

from services.inventory.app.db.connection import connect, init_database
from services.inventory.app.reservation.service import RequestOutcome, process_reservation_request
from shared.canonical_json import canonical_json
from shared.envelope import build_envelope, parse_message
from shared.events import EventType


class RecordingPublisher:
    def __init__(self) -> None:
        self.published: list[dict] = []

    def publish(self, envelope: dict) -> None:
        self.published.append(envelope)


class FailingOncePublisher(RecordingPublisher):
    """Simula queda do worker entre o commit e a publicação da resposta."""

    def __init__(self) -> None:
        super().__init__()
        self.failed = False

    def publish(self, envelope: dict) -> None:
        if not self.failed:
            self.failed = True
            raise ConnectionError("broker unreachable")
        super().publish(envelope)


@pytest.fixture
def connection(tmp_path) -> sqlite3.Connection:
    path = tmp_path / "inventory.db"
    init_database(path)
    conn = connect(path)
    yield conn
    conn.close()


def _request(target: str = "inventory.primary", *, event_seq: int = 2, attempt_number: int = 1):
    envelope = build_envelope(
        execution_id="PILOT_TEST",
        task_id="TASK_000001",
        event_type=EventType.STOCK_RESERVATION_REQUESTED,
        event_seq=event_seq,
        attempt_number=attempt_number,
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


def test_fallback_route_reserves_through_the_same_service(connection):
    publisher = RecordingPublisher()

    result = process_reservation_request(connection, *_request("inventory.fallback"), publisher)

    assert result.outcome == RequestOutcome.RESERVED
    assert connection.execute("SELECT route FROM reservations").fetchone()[0] == "fallback"
    assert publisher.published[0]["payload"]["route"] == "fallback"
    assert publisher.published[0]["target"] == "inventory.fallback"


def test_fallback_for_an_already_reserved_task_does_not_reserve_again(connection):
    publisher = RecordingPublisher()
    process_reservation_request(connection, *_request("inventory.primary"), publisher)

    result = process_reservation_request(connection, *_request("inventory.fallback", event_seq=5), publisher)

    assert result.outcome == RequestOutcome.ALREADY_RESERVED
    assert _count(connection, "reservations") == 1
    assert publisher.published[-1]["payload"]["route"] == "primary"  # reserva existente


def _count(connection: sqlite3.Connection, table: str) -> int:
    return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_first_delivery_is_reserved(connection):
    result = process_reservation_request(connection, *_request(), RecordingPublisher())

    assert result.outcome == RequestOutcome.RESERVED


def test_redelivery_does_not_reserve_again(connection):
    request, payload = _request()
    publisher = RecordingPublisher()

    process_reservation_request(connection, request, payload, publisher)
    result = process_reservation_request(connection, request, payload, publisher)

    assert result.outcome == RequestOutcome.DUPLICATE_MESSAGE
    assert _count(connection, "reservations") == 1
    assert _count(connection, "processed_messages") == 1


def test_redelivery_reemits_the_same_reply(connection):
    request, payload = _request()
    publisher = RecordingPublisher()

    process_reservation_request(connection, request, payload, publisher)
    process_reservation_request(connection, request, payload, publisher)

    first, second = publisher.published
    assert second == first  # mesmo message_id: o Orders deduplica


def test_reply_lost_after_commit_is_recovered_by_redelivery(connection):
    request, payload = _request()
    publisher = FailingOncePublisher()

    with pytest.raises(ConnectionError):
        process_reservation_request(connection, request, payload, publisher)
    assert _count(connection, "reservations") == 1  # reserva já commitada
    assert publisher.published == []

    result = process_reservation_request(connection, request, payload, publisher)

    assert result.outcome == RequestOutcome.DUPLICATE_MESSAGE
    assert publisher.published == [result.reply]
    assert _count(connection, "reservations") == 1


def test_processed_message_stores_result_and_reply(connection):
    request, payload = _request()
    result = process_reservation_request(connection, request, payload, RecordingPublisher())

    row = connection.execute("SELECT * FROM processed_messages").fetchone()
    assert row["event_type"] == "STOCK_RESERVATION_REQUESTED"
    assert row["result"] == "succeeded"
    assert row["response_json"] == canonical_json(result.reply)


def _new_attempt():
    """Nova tentativa lógica (RETRY): novo message_id, mesmo task_id, novo event_seq, attempt + 1."""
    return _request(event_seq=4, attempt_number=2)


def test_new_attempt_of_reserved_task_does_not_reserve_again(connection):
    publisher = RecordingPublisher()
    first, first_payload = _request()
    retry, retry_payload = _new_attempt()
    assert retry.message_id != first.message_id and retry.task_id == first.task_id

    process_reservation_request(connection, first, first_payload, publisher)
    result = process_reservation_request(connection, retry, retry_payload, publisher)

    assert result.outcome == RequestOutcome.ALREADY_RESERVED
    assert _count(connection, "reservations") == 1
    assert _count(connection, "processed_messages") == 2


def test_new_attempt_gets_its_own_success_reply(connection):
    publisher = RecordingPublisher()
    process_reservation_request(connection, *_request(), publisher)
    retry, retry_payload = _new_attempt()

    result = process_reservation_request(connection, retry, retry_payload, publisher)

    first_reply, retry_reply = publisher.published
    assert retry_reply == result.reply
    assert retry_reply["message_id"] != first_reply["message_id"]
    assert retry_reply["event_type"] == "STOCK_RESERVATION_SUCCEEDED"
    assert retry_reply["attempt_number"] == 2
    assert retry_reply["event_seq"] == retry.event_seq
    assert retry_reply["payload"] == first_reply["payload"]


def test_redelivery_of_the_new_attempt_is_still_deduplicated(connection):
    publisher = RecordingPublisher()
    process_reservation_request(connection, *_request(), publisher)
    retry, retry_payload = _new_attempt()

    process_reservation_request(connection, retry, retry_payload, publisher)
    result = process_reservation_request(connection, retry, retry_payload, publisher)

    assert result.outcome == RequestOutcome.DUPLICATE_MESSAGE
    assert publisher.published[2] == publisher.published[1]
    assert _count(connection, "reservations") == 1


def test_task_id_uniqueness_is_enforced_by_the_database(connection):
    connection.execute(
        "INSERT INTO reservations (task_id, order_id, status, route, items_json, created_at)"
        " VALUES ('TASK_000001', 'ORD_000001', 'RESERVED', 'primary', '[]', 'now')"
    )

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO reservations (task_id, order_id, status, route, items_json, created_at)"
            " VALUES ('TASK_000001', 'ORD_000001', 'RESERVED', 'primary', '[]', 'now')"
        )
