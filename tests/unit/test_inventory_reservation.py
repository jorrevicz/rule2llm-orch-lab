import json
import sqlite3

import pytest

from services.inventory.app.db.connection import connect, init_database
from services.inventory.app.reservation.processing import ProcessingSimulator
from services.inventory.app.reservation.service import RequestOutcome, process_reservation_request
from scripts.datasets.generate_dataset import CATALOG_PATH
from shared.catalog import load_catalog
from shared.faults import FaultEventRecorder
from tests.unit.test_faults import control
from shared.canonical_json import canonical_json
from shared.envelope import build_envelope, parse_message
from shared.events import EventType


def _process(connection, request, payload, publisher):
    return process_reservation_request(connection, request, payload, publisher, ProcessingSimulator(0))


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
    init_database(path, load_catalog(CATALOG_PATH))
    conn = connect(path)
    yield conn
    conn.close()


def _request(
    target: str = "inventory.primary",
    *,
    event_seq: int = 2,
    attempt_number: int = 1,
    items: list[dict] | None = None,
):
    envelope = build_envelope(
        execution_id="PILOT_TEST",
        task_id="TASK_000001",
        event_type=EventType.STOCK_RESERVATION_REQUESTED,
        event_seq=event_seq,
        attempt_number=attempt_number,
        target=target,
        payload={"order_id": "ORD_000001", "items": items or [{"sku": "SKU-001", "quantity": 2}]},
    )
    return parse_message(envelope, {EventType.STOCK_RESERVATION_REQUESTED})


def test_primary_reservation_is_persisted(connection):
    request, payload = _request()

    _process(connection, request, payload, RecordingPublisher())

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

    _process(connection, request, payload, publisher)

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
        _process(connection, *_request(), publisher)

    assert publisher.published == []
    assert connection.execute("SELECT COUNT(*) FROM reservations").fetchone()[0] == 0


def test_fallback_route_reserves_through_the_same_service(connection):
    publisher = RecordingPublisher()

    result = _process(connection, *_request("inventory.fallback"), publisher)

    assert result.outcome == RequestOutcome.RESERVED
    assert connection.execute("SELECT route FROM reservations").fetchone()[0] == "fallback"
    assert publisher.published[0]["payload"]["route"] == "fallback"
    assert publisher.published[0]["target"] == "inventory.fallback"


def test_fallback_for_an_already_reserved_task_does_not_reserve_again(connection):
    publisher = RecordingPublisher()
    _process(connection, *_request("inventory.primary"), publisher)

    result = _process(connection, *_request("inventory.fallback", event_seq=5), publisher)

    assert result.outcome == RequestOutcome.ALREADY_RESERVED
    assert _count(connection, "reservations") == 1
    assert publisher.published[-1]["payload"]["route"] == "primary"  # reserva existente


def _count(connection: sqlite3.Connection, table: str) -> int:
    return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_first_delivery_is_reserved(connection):
    result = _process(connection, *_request(), RecordingPublisher())

    assert result.outcome == RequestOutcome.RESERVED


def test_redelivery_does_not_reserve_again(connection):
    request, payload = _request()
    publisher = RecordingPublisher()

    _process(connection, request, payload, publisher)
    result = _process(connection, request, payload, publisher)

    assert result.outcome == RequestOutcome.DUPLICATE_MESSAGE
    assert _count(connection, "reservations") == 1
    assert _count(connection, "processed_messages") == 1


def test_redelivery_reemits_the_same_reply(connection):
    request, payload = _request()
    publisher = RecordingPublisher()

    _process(connection, request, payload, publisher)
    _process(connection, request, payload, publisher)

    first, second = publisher.published
    assert second == first  # mesmo message_id: o Orders deduplica


def test_reply_lost_after_commit_is_recovered_by_redelivery(connection):
    request, payload = _request()
    publisher = FailingOncePublisher()

    with pytest.raises(ConnectionError):
        _process(connection, request, payload, publisher)
    assert _count(connection, "reservations") == 1  # reserva já commitada
    assert publisher.published == []

    result = _process(connection, request, payload, publisher)

    assert result.outcome == RequestOutcome.DUPLICATE_MESSAGE
    assert publisher.published == [result.reply]
    assert _count(connection, "reservations") == 1


def test_processed_message_stores_result_and_reply(connection):
    request, payload = _request()
    result = _process(connection, request, payload, RecordingPublisher())

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

    _process(connection, first, first_payload, publisher)
    result = _process(connection, retry, retry_payload, publisher)

    assert result.outcome == RequestOutcome.ALREADY_RESERVED
    assert _count(connection, "reservations") == 1
    assert _count(connection, "processed_messages") == 2


def test_new_attempt_gets_its_own_success_reply(connection):
    publisher = RecordingPublisher()
    _process(connection, *_request(), publisher)
    retry, retry_payload = _new_attempt()

    result = _process(connection, retry, retry_payload, publisher)

    first_reply, retry_reply = publisher.published
    assert retry_reply == result.reply
    assert retry_reply["message_id"] != first_reply["message_id"]
    assert retry_reply["event_type"] == "STOCK_RESERVATION_SUCCEEDED"
    assert retry_reply["attempt_number"] == 2
    assert retry_reply["event_seq"] == retry.event_seq
    assert retry_reply["payload"] == first_reply["payload"]


def test_redelivery_of_the_new_attempt_is_still_deduplicated(connection):
    publisher = RecordingPublisher()
    _process(connection, *_request(), publisher)
    retry, retry_payload = _new_attempt()

    _process(connection, retry, retry_payload, publisher)
    result = _process(connection, retry, retry_payload, publisher)

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


# -- catálogo de SKUs e dados inconsistentes (D-03) ----------------------------------

UNKNOWN_ITEMS = [{"sku": "SKU-001", "quantity": 1}, {"sku": "SKU-999", "quantity": 1}]


def test_catalog_is_loaded_once_and_reloading_is_harmless(tmp_path):
    path = tmp_path / "inventory.db"
    init_database(path, ["SKU-001", "SKU-002"])
    init_database(path, ["SKU-001", "SKU-002"])

    conn = connect(path)
    assert [row[0] for row in conn.execute("SELECT sku FROM stock ORDER BY sku")] == ["SKU-001", "SKU-002"]
    conn.close()


@pytest.mark.parametrize("target", ["inventory.primary", "inventory.fallback"])
def test_unknown_sku_is_invalid_data_on_any_route(connection, target):
    publisher = RecordingPublisher()

    result = _process(connection, *_request(target, items=UNKNOWN_ITEMS), publisher)

    assert result.outcome == RequestOutcome.INVALID_DATA
    assert _count(connection, "reservations") == 0
    [reply] = publisher.published
    assert reply["event_type"] == "STOCK_RESERVATION_FAILED"
    assert reply["payload"] == {"order_id": "ORD_000001", "route": target.split(".")[1], "failure_reason": "invalid_data"}
    assert (reply["event_seq"], reply["target"]) == (2, target)
    parse_message(reply, {EventType.STOCK_RESERVATION_FAILED})  # dentro do contrato


def test_invalid_data_reply_is_stored_and_reemitted_on_redelivery(connection):
    request, payload = _request(items=UNKNOWN_ITEMS)
    publisher = RecordingPublisher()
    first = _process(connection, request, payload, publisher)

    again = _process(connection, request, payload, publisher)

    row = connection.execute("SELECT result, response_json FROM processed_messages").fetchone()
    assert (row["result"], row["response_json"]) == ("failed", canonical_json(first.reply))
    assert again.outcome == RequestOutcome.DUPLICATE_MESSAGE
    assert publisher.published == [first.reply, first.reply]


# -- tempo de serviço simulado (D-22) e rotas em processos distintos (D-21) ---------


def test_service_time_is_spent_before_and_outside_the_transaction(connection, tmp_path):
    slept = []

    def sleep_while_another_process_writes(seconds: float) -> None:
        slept.append(seconds)
        other = connect(tmp_path / "inventory.db")  # o outro processo (a outra rota)
        other.execute("BEGIN IMMEDIATE")  # falharia se o lock estivesse preso
        other.execute("COMMIT")
        other.close()

    simulator = ProcessingSimulator(100, sleep=sleep_while_another_process_writes)
    result = process_reservation_request(connection, *_request(), RecordingPublisher(), simulator)

    assert slept == [0.1]
    assert result.outcome == RequestOutcome.RESERVED


def test_zero_service_time_does_not_sleep():
    slept = []
    ProcessingSimulator(0, sleep=slept.append).before_reservation(_request()[0])
    assert slept == []


# -- falha injetada na rota primária (D-20) -----------------------------------------


def _faulty(tmp_path, **overrides):
    slept = []
    recorder = FaultEventRecorder.for_process(tmp_path, "inventory-primary", "PILOT_TEST")
    simulator = ProcessingSimulator(
        100,
        fault_source=lambda: control(failure_probability=1.0, **overrides),
        fault_recorder=recorder,
        sleep=slept.append,
    )
    return simulator, slept


def _fault_events(tmp_path):
    path = tmp_path / "fault_events.inventory-primary.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_intermittent_error_fails_the_primary_request_without_reserving(connection, tmp_path):
    simulator, _ = _faulty(tmp_path)
    publisher = RecordingPublisher()
    request, payload = _request()

    result = process_reservation_request(connection, request, payload, publisher, simulator)

    assert result.outcome == RequestOutcome.TRANSIENT_ERROR
    assert _count(connection, "reservations") == 0
    assert result.reply["payload"]["failure_reason"] == "transient_error"
    [event] = _fault_events(tmp_path)
    assert (event["event_type"], event["task_id"], event["message_id"]) == ("FAULT_APPLIED", "TASK_000001", request.message_id)
    assert event["effect"] == "transient_error"


def test_redelivery_of_a_failed_request_reemits_the_same_failure(connection, tmp_path):
    simulator, _ = _faulty(tmp_path)
    publisher = RecordingPublisher()
    request, payload = _request()
    first = process_reservation_request(connection, request, payload, publisher, simulator)

    again = process_reservation_request(connection, request, payload, publisher, simulator)

    assert again.outcome == RequestOutcome.DUPLICATE_MESSAGE
    assert publisher.published == [first.reply, first.reply]


def test_fallback_route_is_not_affected_by_a_primary_fault(connection, tmp_path):
    simulator, slept = _faulty(tmp_path, type="timeout", delay_ms=3000)

    result = process_reservation_request(connection, *_request("inventory.fallback"), RecordingPublisher(), simulator)

    assert result.outcome == RequestOutcome.RESERVED
    assert slept == [0.1]  # só o tempo de serviço
    assert _fault_events(tmp_path) == []


def test_timeout_delays_the_primary_request_then_reserves(connection, tmp_path):
    simulator, slept = _faulty(tmp_path, type="timeout", delay_ms=3000)

    result = process_reservation_request(connection, *_request(), RecordingPublisher(), simulator)

    assert result.outcome == RequestOutcome.RESERVED  # resposta tardia
    assert slept == [0.1, 3.0]
    assert _fault_events(tmp_path)[0]["effect"] == "delay_ms=3000"


def test_request_not_drawn_is_processed_normally(connection, tmp_path):
    simulator = ProcessingSimulator(0, fault_source=lambda: control(failure_probability=0.0))

    result = process_reservation_request(connection, *_request(), RecordingPublisher(), simulator)

    assert result.outcome == RequestOutcome.RESERVED


def test_injected_failure_comes_before_business_idempotency(connection, tmp_path):
    _process(connection, *_request(), RecordingPublisher())  # tentativa 1 reservou
    simulator, _ = _faulty(tmp_path)

    result = process_reservation_request(connection, *_new_attempt(), RecordingPublisher(), simulator)

    assert result.outcome == RequestOutcome.TRANSIENT_ERROR  # a rota falhou antes de processar
    assert _count(connection, "reservations") == 1
