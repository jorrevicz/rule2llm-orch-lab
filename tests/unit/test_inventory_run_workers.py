"""Um processo Celery por rota do inventory-service, no mesmo container (D-21)."""

from services.inventory.app.run_workers import ROUTE_WORKERS, worker_command


def test_each_route_has_its_own_worker_process():
    assert sorted(str(queue) for queue in ROUTE_WORKERS.values()) == ["inventory.fallback", "inventory.primary"]
    assert len(set(ROUTE_WORKERS)) == 2


def test_worker_consumes_only_its_route_without_cluster_coordination():
    command = worker_command("inventory-primary", "inventory.primary")

    assert "--queues=inventory.primary" in command
    assert "--concurrency=1" in command
    assert "--hostname=inventory-primary@%h" in command
    assert {"--without-mingle", "--without-gossip", "--without-heartbeat"} <= set(command)
