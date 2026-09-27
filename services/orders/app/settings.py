"""Configuração de runtime do orders-service, lida de variáveis de ambiente.

Parâmetros experimentais não ficam aqui: a fonte de verdade é
config/experiment_config.yml (ver shared.config).
"""

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BROKER_URL = "amqp://tcc:tcc@localhost:5672//"
DEFAULT_DATABASE_PATH = "orders.db"
# Identificador provisório para execuções de desenvolvimento; o ciclo de vida do
# execution_id é formalizado em M3-T03.
DEFAULT_EXECUTION_ID = "PILOT_0000"


@dataclass(frozen=True)
class Settings:
    broker_url: str
    database_path: Path
    execution_id: str


def load_settings() -> Settings:
    return Settings(
        broker_url=os.environ.get("BROKER_URL", DEFAULT_BROKER_URL),
        database_path=Path(os.environ.get("ORDERS_DB_PATH", DEFAULT_DATABASE_PATH)),
        execution_id=os.environ.get("EXECUTION_ID", DEFAULT_EXECUTION_ID),
    )
