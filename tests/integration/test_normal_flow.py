"""Fluxo normal ponta a ponta contra o ambiente Docker Compose (M1).

Executar com o ambiente no ar:

    docker compose up -d --build --wait
    .venv/bin/python -m pytest -m integration
"""

import pytest

from scripts.pilot.smoke_test import run

pytestmark = pytest.mark.integration


def test_single_order_completes_end_to_end():
    result = run(orders=1)

    assert result.ok, result.errors


def test_several_orders_complete_with_one_reservation_each():
    result = run(orders=5)

    assert result.ok, result.errors
    assert set(result.orders.values()) == {"COMPLETED"}
    assert set(result.reservations.values()) == {1}
