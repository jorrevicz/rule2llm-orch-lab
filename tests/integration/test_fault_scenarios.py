"""Perturbações do M6 contra o ambiente Docker Compose (D-03, D-20, D-21).

Executar com o ambiente no ar e `decision_engine: RULES` (sequências exigidas são as
do Rules; com o LLM, as decisões são o que se observa, M6-T10):

    .venv/bin/python -m pytest -m integration
"""

import json
import time

import pytest

from scripts.faults.injectors import PrimaryRouteFault, WRITER
from scripts.pilot.check_traceability import DEFAULT_DATA_ROOT
from scripts.pilot.decision_scenarios import decisions_of
from scripts.pilot.environment import (
    DEFAULT_BASE_URL,
    INVENTORY_DB,
    compose,
    create_order,
    query_sqlite,
    wait_for_terminal_status,
)
from shared.artifacts import execution_dir
from shared.config import FaultSection
from shared.faults import FaultEventRecorder
from tests.integration.test_decision_scenarios import CONFIG, _engine_in_use

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif("_engine_in_use() != 'RULES'", reason="sequências exigidas são as do Rules"),
]


def _execution_dir():
    execution_id = compose("exec", "-T", "orders-api", "printenv", "EXECUTION_ID").strip()
    return execution_id, execution_dir(DEFAULT_DATA_ROOT, execution_id)


def _faults_applied(directory, task_id: str) -> list[dict]:
    records = []
    for shard in directory.glob("fault_events.inventory-*.jsonl"):
        records += [json.loads(line) for line in shard.read_text().splitlines()]
    return [record for record in records if record.get("task_id") == task_id]


def _reservation_routes(task_id: str) -> list[str]:
    return [row[0] for row in query_sqlite(INVENTORY_DB, "SELECT route FROM reservations WHERE task_id = ?", task_id)]


def test_primary_route_degradation_leaves_the_fallback_executable():
    execution_id, directory = _execution_dir()
    fault = FaultSection(
        type="intermittent_error",
        target="inventory.primary",
        start_after_seconds=0,
        duration_seconds=1,
        failure_probability=1.0,  # toda tentativa na rota primária falha
        seed=1,
    )
    start = time.monotonic()
    injector = PrimaryRouteFault(
        fault, FaultEventRecorder.for_process(directory, WRITER, execution_id), lambda: time.monotonic() - start, directory
    )
    injector.start()
    try:
        order = create_order(DEFAULT_BASE_URL, [{"sku": "SKU-004", "quantity": 1}])
        status = wait_for_terminal_status(DEFAULT_BASE_URL, order["order_id"], timeout_s=30)
    finally:
        injector.stop()
    time.sleep(1)

    max_attempts = CONFIG.messaging.max_attempts
    assert status == "COMPLETED"
    assert decisions_of(order["task_id"]) == (
        [("CONTINUE", "NORMAL_FLOW")]
        + [("RETRY", "TRANSIENT_RETRY")] * (max_attempts - 1)
        + [("FALLBACK", "PRIMARY_EXHAUSTED")]
    )
    assert _reservation_routes(order["task_id"]) == ["fallback"]
    applied = _faults_applied(directory, order["task_id"])
    assert sorted(event["attempt_number"] for event in applied) == list(range(1, max_attempts + 1))


def test_unknown_sku_is_aborted_as_invalid_data():
    order = create_order(DEFAULT_BASE_URL, [{"sku": "SKU-001", "quantity": 1}, {"sku": "SKU-999", "quantity": 1}])

    status = wait_for_terminal_status(DEFAULT_BASE_URL, order["order_id"], timeout_s=15)
    time.sleep(1)

    assert status == "FAILED"
    assert decisions_of(order["task_id"]) == [("CONTINUE", "NORMAL_FLOW"), ("ABORT", "INVALID_DATA")]
    assert _reservation_routes(order["task_id"]) == []
