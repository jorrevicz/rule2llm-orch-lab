"""Cenários de decisão ponta a ponta contra o ambiente Docker Compose (M4).

Executar com o ambiente no ar e `decision_engine: RULES`:

    .venv/bin/python -m pytest -m integration
"""

import pytest

from scripts.pilot.decision_scenarios import inventory_down, inventory_paused
from scripts.pilot.environment import compose
from tests.factories import repo_config

CONFIG = repo_config()


def _engine_in_use() -> str:
    code = "from shared.config import load_experiment_config as l; print(l().experiment.decision_engine)"
    return compose("exec", "-T", "orders-worker", "python", "-c", code).strip()


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif("_engine_in_use() != 'RULES'", reason="sequências exigidas são as do Rules"),
]


def test_inventory_down_waits_then_aborts():
    result = inventory_down(max_waits=CONFIG.messaging.max_waits)

    assert result.ok, result.errors


def test_inventory_paused_retries_falls_back_then_aborts():
    result = inventory_paused(max_attempts=CONFIG.messaging.max_attempts)

    assert result.ok, result.errors
