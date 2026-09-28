"""Idempotência ponta a ponta contra o ambiente Docker Compose (M2).

Executar com o ambiente no ar:

    docker compose up -d --build --wait
    .venv/bin/python -m pytest -m integration
"""

import pytest

from scripts.pilot.duplicate_message_test import run

pytestmark = pytest.mark.integration


def test_new_attempt_and_redelivery_do_not_duplicate_the_reservation():
    result = run()

    assert result.ok, result.errors
