"""Dependências da camada de coordenação do orders-service, montadas uma vez por processo.

Não é um terceiro serviço: é o conjunto de componentes internos que a API e o worker
do orders-service usam nos pontos de decisão. O Orchestrator (M4-T04) acrescenta
aqui o motor de decisão, o validador, o executor e o registro de decisões.
"""

from dataclasses import dataclass

from celery import Celery

from services.orders.app.messaging.publisher import CeleryCommandPublisher, CommandPublisher
from services.orders.app.observability.recorders import StateRecorder
from services.orders.app.orchestration.broker_observer import AmqpBrokerObserver
from services.orders.app.orchestration.state_builder import StateBuilder
from services.orders.app.settings import Settings
from shared.config import ExperimentConfig


@dataclass(frozen=True)
class Coordination:
    publisher: CommandPublisher
    state_builder: StateBuilder
    state_recorder: StateRecorder


def build_coordination(settings: Settings, config: ExperimentConfig, app: Celery) -> Coordination:
    return Coordination(
        publisher=CeleryCommandPublisher(app),
        state_builder=StateBuilder(config, AmqpBrokerObserver(app)),
        state_recorder=StateRecorder.for_process(settings.artifacts_dir, settings.service_role),
    )
