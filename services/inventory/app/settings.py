"""Configuração de runtime do inventory-service, lida de variáveis de ambiente.

Parâmetros experimentais não ficam aqui: a fonte de verdade é
config/experiment_config.yml (ver shared.config).
"""

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BROKER_URL = "amqp://tcc:tcc@localhost:5672//"
DEFAULT_DATABASE_PATH = "inventory.db"


@dataclass(frozen=True)
class Settings:
    broker_url: str
    database_path: Path


def load_settings() -> Settings:
    return Settings(
        broker_url=os.environ.get("BROKER_URL", DEFAULT_BROKER_URL),
        database_path=Path(os.environ.get("INVENTORY_DB_PATH", DEFAULT_DATABASE_PATH)),
    )
