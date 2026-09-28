"""Conexões SQLite do inventory-service. `inventory.db` é exclusivo deste serviço."""

from pathlib import Path

from services.inventory.app.db.models import SCHEMA
from shared.sqlite import connect, transaction
from shared.sqlite import init_database as _init_database

__all__ = ["connect", "init_database", "transaction"]


def init_database(path: Path) -> None:
    _init_database(path, SCHEMA)
