"""Montagem da camada de coordenação do orders-service, uma vez por processo.

Não é um terceiro serviço: é o conjunto de componentes internos que a API e o worker
do orders-service usam nos pontos de decisão. O motor é escolhido por
`experiment.decision_engine` na configuração (RF-016); todo o resto é comum às duas
abordagens (RNF-009, RNF-010, RNF-019).
"""

from dataclasses import dataclass

from celery import Celery

from services.orders.app.messaging.publisher import CeleryCommandPublisher
from services.orders.app.observability.recorders import DecisionRecorder, StateRecorder
from services.orders.app.orchestration.broker_observer import AmqpBrokerObserver
from services.orders.app.orchestration.decision_engine import DecisionEngine
from services.orders.app.orchestration.executor import DecisionExecutor
from services.orders.app.orchestration.orchestrator import Orchestrator
from services.orders.app.orchestration.rules_engine import RulesDecisionEngine
from services.orders.app.orchestration.scheduler import CeleryTaskScheduler
from services.orders.app.orchestration.state_builder import StateBuilder
from services.orders.app.orchestration.validator import DecisionValidator
from services.orders.app.settings import Settings
from shared.config import ExperimentConfig


@dataclass(frozen=True)
class Coordination:
    orchestrator: Orchestrator


def build_engine(config: ExperimentConfig) -> DecisionEngine:
    if config.experiment.decision_engine == "RULES":
        return RulesDecisionEngine(config)
    raise NotImplementedError("LLMDecisionEngine is implemented in M5")


def build_coordination(settings: Settings, config: ExperimentConfig, app: Celery) -> Coordination:
    return Coordination(
        orchestrator=Orchestrator(
            state_builder=StateBuilder(config, AmqpBrokerObserver(app)),
            engine=build_engine(config),
            validator=DecisionValidator(config),
            executor=DecisionExecutor(CeleryCommandPublisher(app), CeleryTaskScheduler(app), config),
            state_recorder=StateRecorder.for_process(settings.artifacts_dir, settings.service_role),
            decision_recorder=DecisionRecorder.for_process(settings.artifacts_dir, settings.service_role),
        )
    )
