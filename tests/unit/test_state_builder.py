import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from services.orders.app.db.connection import connect, init_database, transaction
from services.orders.app.db.repositories import create_order_with_task
from services.orders.app.db.trajectory import EventSource, record_event
from services.orders.app.orchestration.broker_observer import QueueStats
from services.orders.app.orchestration.event_handler import handle_inventory_event
from services.orders.app.orchestration.state_builder import StateBuilder
from shared.config import load_experiment_config
from shared.envelope import MessageEnvelope, build_envelope
from shared.events import EventType
from shared.messaging import Route
from shared.system_state import SystemState
from shared.timestamps import parse_iso, to_iso
from tests.factories import Harness

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "experiment_config.yml"
CREATED_AT = "2026-09-28T12:00:00.000Z"


class FakeObserver:
    def __init__(self) -> None:
        self.stats = {
            Route.INVENTORY_PRIMARY: QueueStats(message_count=4, consumer_count=1),
            Route.INVENTORY_FALLBACK: QueueStats(message_count=0, consumer_count=1),
        }

    def queue_stats(self, route: Route) -> QueueStats:
        return self.stats[route]


class Clock:
    def __init__(self, iso: str) -> None:
        self.now = parse_iso(iso)

    def __call__(self):
        return self.now

    def advance(self, ms: int) -> None:
        self.now += timedelta(milliseconds=ms)


@pytest.fixture
def config():
    return load_experiment_config(REPO_CONFIG)


@pytest.fixture
def observer() -> FakeObserver:
    return FakeObserver()


@pytest.fixture
def clock() -> Clock:
    return Clock("2026-09-28T12:00:01.500Z")


@pytest.fixture
def builder(config, observer, clock) -> StateBuilder:
    return StateBuilder(config, observer, clock)


@pytest.fixture
def connection(tmp_path) -> sqlite3.Connection:
    path = tmp_path / "orders.db"
    init_database(path)
    conn = connect(path)
    create_order_with_task(
        conn, execution_id="PILOT_TEST", items_json='[{"quantity":1,"sku":"SKU-001"}]', now=CREATED_AT
    )
    yield conn
    conn.close()


TASK = "TASK_000001"


def _dispatch(connection: sqlite3.Connection) -> dict:
    harness = Harness(connection=connection, directory=Path("/nonexistent"))
    return harness.dispatch(TASK)


def _success_reply(connection: sqlite3.Connection) -> MessageEnvelope:
    request_seq = connection.execute("SELECT current_event_seq FROM tasks").fetchone()[0]
    return MessageEnvelope.model_validate(
        build_envelope(
            execution_id="PILOT_TEST",
            task_id=TASK,
            event_type=EventType.STOCK_RESERVATION_SUCCEEDED,
            event_seq=request_seq,
            attempt_number=1,
            target="inventory.primary",
            payload={"order_id": "ORD_000001", "route": "primary"},
        )
    )


def test_state_of_a_new_task(builder, connection, config):
    state = builder.build(connection, TASK)

    assert isinstance(state, SystemState)
    assert state.execution_id == "PILOT_TEST"
    assert state.state_id.startswith("STATE_")
    assert state.current_event_seq == 1
    assert state.task.phase == "PENDING"
    assert state.task.current_target is None
    assert state.task.current_service is None
    assert state.task.attempt_number == 1
    assert state.task.max_attempts == config.messaging.max_attempts
    assert state.task.max_waits == config.messaging.max_waits
    assert state.task.elapsed_ms == 1500
    assert state.service.latency_ms is None
    assert state.service.last_result is None
    assert [event.event_type for event in state.recent_events] == ["TASK_CREATED"]


def test_each_snapshot_has_its_own_state_id(builder, connection):
    assert builder.build(connection, TASK).state_id != builder.build(connection, TASK).state_id


def test_queue_size_comes_from_the_current_target_queue(builder, connection, observer):
    observer.stats[Route.INVENTORY_PRIMARY] = QueueStats(message_count=25, consumer_count=1)

    assert builder.build(connection, TASK).messaging.queue_size == 25


@pytest.mark.parametrize(
    ("consumers", "last_result", "expected"),
    [
        (1, None, "available"),
        (1, "ok", "available"),
        (1, "timeout", "degraded"),
        (1, "transient_error", "degraded"),
        (0, None, "unavailable"),
        (0, "timeout", "unavailable"),
    ],
)
def test_service_status(builder, connection, observer, consumers, last_result, expected):
    observer.stats[Route.INVENTORY_PRIMARY] = QueueStats(message_count=0, consumer_count=consumers)
    connection.execute("UPDATE tasks SET last_result = ?", (last_result,))

    assert builder.build(connection, TASK).service.status == expected


def test_fallback_is_available_only_when_its_route_has_a_consumer(builder, connection, observer):
    state = builder.build(connection, TASK)
    assert state.alternatives.fallback_available is True
    assert state.alternatives.alternative_targets == ["inventory.fallback"]

    observer.stats[Route.INVENTORY_FALLBACK] = QueueStats(message_count=0, consumer_count=0)
    state = builder.build(connection, TASK)
    assert state.alternatives.fallback_available is False
    assert state.alternatives.alternative_targets == []


def test_used_fallback_is_not_offered_again(builder, connection):
    connection.execute("UPDATE tasks SET fallback_used = 1")

    state = builder.build(connection, TASK)

    assert state.alternatives.fallback_used is True
    assert state.alternatives.alternative_targets == []


def test_latency_of_a_pending_request_grows_until_the_reply(builder, connection, clock):
    envelope = _dispatch(connection)
    clock.now = parse_iso(envelope["published_at"]) + timedelta(milliseconds=2054)

    state = builder.build(connection, TASK)

    assert state.task.phase == "DISPATCHED"
    assert state.task.current_target == "inventory.primary"
    assert state.task.current_service == "inventory-service"
    assert state.service.latency_ms == 2054


def test_latency_stops_at_the_reply(builder, connection, clock):
    _dispatch(connection)
    handle_inventory_event(connection, _success_reply(connection))
    clock.advance(60_000)

    latency = builder.build(connection, TASK).service.latency_ms

    assert latency is not None and latency < 60_000


def test_recent_events_window_keeps_only_the_last_k(builder, connection, config):
    limit = config.context.recent_events_limit
    with transaction(connection):
        for seq in range(2, limit + 4):
            record_event(
                connection,
                execution_id="PILOT_TEST",
                task_id=TASK,
                event_seq=seq,
                event_type=EventType.WAIT_SCHEDULED,
                attempt_number=1,
                service=EventSource.ORDERS,
                now=to_iso(parse_iso(CREATED_AT)),
            )

    window = builder.build(connection, TASK).recent_events

    assert len(window) == limit
    assert [event.event_seq for event in window] == list(range(4, limit + 4))


def test_repeated_deliveries_do_not_occupy_the_window(builder, connection):
    _dispatch(connection)
    reply = _success_reply(connection)
    handle_inventory_event(connection, reply)
    handle_inventory_event(connection, reply)
    handle_inventory_event(connection, reply)

    state = builder.build(connection, TASK)

    assert [event.event_type for event in state.recent_events] == [
        "TASK_CREATED",
        "STOCK_RESERVATION_REQUESTED",
        "STOCK_RESERVATION_SUCCEEDED",
        "TASK_COMPLETED",
    ]
    assert state.messaging.redelivered is True  # última entrega observada foi repetida


def test_unknown_task_is_an_error(builder, connection):
    with pytest.raises(ValueError):
        builder.build(connection, "TASK_999999")
