"""Rastreabilidade ponta a ponta contra o ambiente Docker Compose (M3).

Executar com o ambiente no ar, idealmente numa execução de piloto aberta:

    eval "$(.venv/bin/python -m scripts.pilot.new_execution)"
    docker compose up -d --build --wait
    .venv/bin/python -m pytest -m integration
"""

import pytest

from scripts.pilot import duplicate_message_test, smoke_test
from scripts.pilot.check_traceability import DEFAULT_DATA_ROOT, DEVELOPMENT_EXECUTION, check
from scripts.pilot.collect_artifacts import collect
from scripts.pilot.environment import compose
from shared.artifacts import execution_dir

pytestmark = pytest.mark.integration


def test_collected_artifacts_are_consistent():
    execution_id = compose("exec", "-T", "orders-api", "printenv", "EXECUTION_ID").strip()
    assert smoke_test.run(orders=2).ok
    assert duplicate_message_test.run().ok

    collect(execution_id)
    result = check(
        execution_dir(DEFAULT_DATA_ROOT, execution_id),
        require_metadata=execution_id != DEVELOPMENT_EXECUTION,
    )

    assert result.ok, result.errors
    assert result.counts["states"] >= 3
    assert result.counts["microservices_logs"] > 0
