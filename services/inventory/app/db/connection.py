"""Conexões SQLite do inventory-service. `inventory.db` é exclusivo deste serviço."""

from collections.abc import Iterable
from pathlib import Path

from services.inventory.app.db.models import SCHEMA
from shared.sqlite import connect, transaction
from shared.sqlite import init_database as _init_database

__all__ = ["connect", "init_database", "transaction"]


def init_database(path: Path, catalog: Iterable[str]) -> None:
    """Cria o esquema e carrega o catálogo de SKUs (D-03); idempotente."""
    _init_database(path, SCHEMA)
    connection = connect(path)
    try:
        with transaction(connection):
            connection.executemany(
                "INSERT OR IGNORE INTO stock (sku) VALUES (?)", [(sku,) for sku in catalog]
            )
    finally:
        connection.close()
