import sqlite3

import pytest
from fastapi.testclient import TestClient

from services.orders.app.main import create_app
from services.orders.app.settings import Settings

VALID_ORDER = {"items": [{"sku": "SKU-002", "quantity": 1}, {"sku": "SKU-001", "quantity": 2}]}


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        broker_url="memory://",
        database_path=tmp_path / "orders.db",
        execution_id="PILOT_TEST",
        data_root=tmp_path / "data",
        service_role="orders-api",
    )


class RecordingPublisher:
    def __init__(self) -> None:
        self.published: list[tuple[dict, str]] = []

    def publish(self, envelope: dict, route) -> None:
        self.published.append((envelope, str(route)))


@pytest.fixture
def publisher() -> RecordingPublisher:
    return RecordingPublisher()


@pytest.fixture
def client(settings, publisher) -> TestClient:
    with TestClient(create_app(settings, publisher)) as test_client:
        yield test_client


def _row(settings: Settings, sql: str, *params) -> sqlite3.Row:
    connection = sqlite3.connect(settings.database_path)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(sql, params).fetchone()
    finally:
        connection.close()


def test_create_order_returns_202_with_identifiers(client):
    response = client.post("/orders", json=VALID_ORDER)

    assert response.status_code == 202
    assert response.json() == {"order_id": "ORD_000001", "task_id": "TASK_000001", "status": "PENDING"}


def test_create_order_persists_order_and_task(client, settings):
    client.post("/orders", json=VALID_ORDER)

    order = _row(settings, "SELECT * FROM orders WHERE order_id = ?", "ORD_000001")
    task = _row(settings, "SELECT * FROM tasks WHERE task_id = ?", "TASK_000001")
    assert order["status"] == "PENDING"
    assert order["execution_id"] == "PILOT_TEST"
    assert task["order_id"] == "ORD_000001"
    assert task["attempt_number"] == 1  # D-14
    assert task["wait_count"] == 0
    assert task["fallback_used"] == 0


def test_items_are_stored_as_canonical_json(client, settings):
    client.post("/orders", json=VALID_ORDER)

    order = _row(settings, "SELECT items_json FROM orders WHERE order_id = ?", "ORD_000001")
    assert order["items_json"] == '[{"quantity":1,"sku":"SKU-002"},{"quantity":2,"sku":"SKU-001"}]'


def test_identifiers_are_sequential(client):
    client.post("/orders", json=VALID_ORDER)
    second = client.post("/orders", json=VALID_ORDER).json()

    assert second["order_id"] == "ORD_000002"
    assert second["task_id"] == "TASK_000002"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"items": []},
        {"items": [{"sku": "", "quantity": 1}]},
        {"items": [{"sku": "SKU-001", "quantity": 0}]},
        {"items": [{"sku": "SKU-001", "quantity": -1}]},
        {"items": [{"sku": "SKU-001", "quantity": "2"}]},
        {"items": [{"sku": "SKU-001"}]},
        {"items": [{"sku": "SKU-001", "quantity": 1, "price": 10}]},
        {"items": [{"sku": "SKU-001", "quantity": 1}], "target": "inventory.fallback"},
    ],
    ids=[
        "no-items-field",
        "empty-items",
        "empty-sku",
        "zero-quantity",
        "negative-quantity",
        "string-quantity",
        "missing-quantity",
        "extra-item-field",
        "extra-order-field",
    ],
)
def test_invalid_order_is_rejected_with_400_and_not_persisted(client, settings, publisher, payload):
    response = client.post("/orders", json=payload)

    assert response.status_code == 400
    assert _row(settings, "SELECT COUNT(*) AS n FROM orders")["n"] == 0
    assert publisher.published == []


def test_created_order_is_dispatched_to_primary_route(client, settings, publisher):
    client.post("/orders", json=VALID_ORDER)

    [(envelope, route)] = publisher.published
    task = _row(settings, "SELECT * FROM tasks WHERE task_id = ?", "TASK_000001")
    assert route == "inventory.primary"
    assert task["status"] == "DISPATCHED"
    assert task["current_target"] == "inventory.primary"
    # D-16: TASK_CREATED = 1, STOCK_RESERVATION_REQUESTED = 2.
    assert task["current_event_seq"] == envelope["event_seq"] == 2


def test_dispatch_envelope_carries_the_order(client, publisher):
    client.post("/orders", json=VALID_ORDER)

    [(envelope, _)] = publisher.published
    assert envelope["schema_version"] == "1.0"
    assert envelope["execution_id"] == "PILOT_TEST"
    assert envelope["task_id"] == "TASK_000001"
    assert envelope["event_type"] == "STOCK_RESERVATION_REQUESTED"
    assert envelope["attempt_number"] == 1
    assert envelope["target"] == "inventory.primary"
    assert envelope["message_id"].startswith("MSG_")
    assert envelope["payload"] == {
        "order_id": "ORD_000001",
        "items": [{"quantity": 1, "sku": "SKU-002"}, {"quantity": 2, "sku": "SKU-001"}],
    }


def test_order_stays_pending_after_dispatch(client):
    response = client.post("/orders", json=VALID_ORDER)

    assert response.json()["status"] == "PENDING"
    assert client.get("/orders/ORD_000001").json()["status"] == "PENDING"


def test_read_order_returns_current_status(client):
    client.post("/orders", json=VALID_ORDER)

    response = client.get("/orders/ORD_000001")

    assert response.status_code == 200
    assert response.json() == {"order_id": "ORD_000001", "task_id": "TASK_000001", "status": "PENDING"}


def test_read_unknown_order_returns_404(client):
    assert client.get("/orders/ORD_999999").status_code == 404


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}
