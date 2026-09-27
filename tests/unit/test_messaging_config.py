import pytest
from celery import Celery

from services.inventory.app.messaging.celery_app import app as inventory_app
from services.orders.app.messaging.celery_app import app as orders_app
from shared.messaging import (
    DEAD_LETTER_QUEUE,
    INVENTORY_RESERVE_TASK,
    TASKS_EXCHANGE,
    Route,
    configure_celery,
)

SERVICE_APPS = [orders_app, inventory_app]


@pytest.fixture
def app() -> Celery:
    celery_app = Celery("test")
    configure_celery(celery_app, "memory://")
    return celery_app


def test_acknowledgement_and_prefetch_policy(app):
    assert app.conf.task_acks_late is True
    assert app.conf.task_reject_on_worker_lost is True
    assert app.conf.worker_prefetch_multiplier == 1


def test_business_queues_are_referenced_without_redeclaration(app):
    queues = {queue.name: queue for queue in app.conf.task_queues}

    for route in Route:
        queue = queues[str(route)]
        assert queue.exchange.name == TASKS_EXCHANGE
        assert queue.routing_key == str(route)
        assert queue.no_declare is True
        assert queue.exchange.no_declare is True


def test_unrouted_tasks_go_to_the_dead_letter_queue(app):
    assert app.conf.task_default_queue == DEAD_LETTER_QUEUE
    assert app.conf.task_create_missing_queues is False


@pytest.mark.parametrize("route", list(Route))
def test_publishing_by_route_uses_tasks_exchange(app, route):
    routed = app.amqp.router.route({"queue": str(route)}, INVENTORY_RESERVE_TASK, (), {})

    assert routed["queue"].exchange.name == TASKS_EXCHANGE
    assert routed["queue"].routing_key == str(route)


def test_unknown_route_is_rejected(app):
    with pytest.raises(Exception, match="missing from task_queues"):
        app.amqp.router.route({"queue": "inventory.unknown"}, INVENTORY_RESERVE_TASK, (), {})


def test_celery_auxiliary_queues_are_disabled(app):
    assert app.conf.worker_enable_remote_control is False
    assert app.conf.worker_send_task_events is False


@pytest.mark.parametrize("service_app", SERVICE_APPS, ids=["orders", "inventory"])
def test_both_services_share_the_same_messaging_policy(service_app, app):
    for key in (
        "task_acks_late",
        "task_reject_on_worker_lost",
        "worker_prefetch_multiplier",
        "task_default_queue",
        "task_create_missing_queues",
    ):
        assert service_app.conf[key] == app.conf[key]
