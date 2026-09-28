"""Configuração de runtime do orders-service, lida de variáveis de ambiente.

Parâmetros experimentais não ficam aqui: a fonte de verdade é
config/experiment_config.yml (ver shared.config).
"""

import os
from dataclasses import dataclass
from pathlib import Path

from shared.artifacts import execution_dir

DEFAULT_BROKER_URL = "amqp://tcc:tcc@localhost:5672//"
DEFAULT_DATA_ROOT = "data"
DEFAULT_SERVICE_ROLE = "orders-api"
DEFAULT_DATABASE_PATH = "orders.db"
# Execução de desenvolvimento, sem metadados. Execuções de piloto registradas são
# abertas por `scripts/pilot/new_execution.py` (PILOT_0001 em diante).
DEFAULT_EXECUTION_ID = "PILOT_0000"


@dataclass(frozen=True)
class Settings:
    broker_url: str
    database_path: Path
    execution_id: str
    data_root: Path
    service_role: str  # identifica o processo que grava os artefatos (api/worker)

    @property
    def artifacts_dir(self) -> Path:
        return execution_dir(self.data_root, self.execution_id)


def load_settings() -> Settings:
    return Settings(
        broker_url=os.environ.get("BROKER_URL", DEFAULT_BROKER_URL),
        database_path=Path(os.environ.get("ORDERS_DB_PATH", DEFAULT_DATABASE_PATH)),
        execution_id=os.environ.get("EXECUTION_ID", DEFAULT_EXECUTION_ID),
        data_root=Path(os.environ.get("DATA_ROOT", DEFAULT_DATA_ROOT)),
        service_role=os.environ.get("SERVICE_ROLE", DEFAULT_SERVICE_ROLE),
    )
