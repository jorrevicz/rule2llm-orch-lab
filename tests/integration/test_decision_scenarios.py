"""Cenários de decisão ponta a ponta contra o ambiente Docker Compose (M4).

Executar com o ambiente no ar e `decision_engine: RULES`:

    .venv/bin/python -m pytest -m integration
"""

import pytest

from scripts.pilot.decision_scenarios import inventory_down, inventory_paused
from shared.config import load_experiment_config

pytestmark = pytest.mark.integration

CONFIG = load_experiment_config()


def test_inventory_down_waits_then_aborts():
    result = inventory_down(max_waits=CONFIG.messaging.max_waits)

    assert result.ok, result.errors


def test_inventory_paused_retries_falls_back_then_aborts():
    result = inventory_paused(max_attempts=CONFIG.messaging.max_attempts)

    assert result.ok, result.errors
