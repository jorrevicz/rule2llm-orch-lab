"""Configuração de runtime do inventory-service, lida de variáveis de ambiente.

Parâmetros experimentais não ficam aqui: a fonte de verdade é
config/experiment_config.yml (ver shared.config).
"""

import os
from dataclasses import dataclass

DEFAULT_BROKER_URL = "amqp://tcc:tcc@localhost:5672//"


@dataclass(frozen=True)
class Settings:
    broker_url: str


def load_settings() -> Settings:
    return Settings(broker_url=os.environ.get("BROKER_URL", DEFAULT_BROKER_URL))
