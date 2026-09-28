"""Fábricas de dados e dublês de infraestrutura para os testes."""

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from services.orders.app.db.connection import connect, init_database
from services.orders.app.db.repositories import create_order_with_task
from services.orders.app.observability.recorders import DecisionRecorder, StateRecorder
from services.orders.app.orchestration.broker_observer import QueueStats
from services.orders.app.orchestration.executor import DecisionExecutor
from services.orders.app.orchestration.orchestrator import Orchestrator
from services.orders.app.orchestration.rules_engine import RulesDecisionEngine
from services.orders.app.orchestration.state_builder import StateBuilder
from services.orders.app.orchestration.validator import DecisionValidator
from shared.artifacts import JsonlWriter
from shared.config import ExperimentConfig, load_experiment_config
from shared.decision import Action, Decision, ReasonCode
from shared.system_state import SystemState
from shared.timestamps import utc_now_iso

REPO_CONFIG = Path(__file__).resolve().parents[1] / "config" / "experiment_config.yml"
ITEMS_JSON = '[{"quantity":1,"sku":"SKU-001"}]'


def repo_config() -> ExperimentConfig:
    return load_experiment_config(REPO_CONFIG)


# -- SYSTEM_STATE ----------------------------------------------------------------------


def make_state(**overrides: Any) -> SystemState:
    """`SYSTEM_STATE` válido de uma tarefa recém-criada; sobrescreva com chaves pontuadas.

    Ex.: make_state(**{"task.phase": "DISPATCHED", "service.last_result": "timeout"})
    """
    state: dict[str, Any] = {
        "execution_id": "PILOT_TEST",
        "task_id": "TASK_000001",
        "state_id": "STATE_TEST",
        "timestamp": "2026-09-28T12:00:00.000Z",
        "current_event_seq": 1,
        "task": {
            "phase": "PENDING",
            "current_service": None,
            "current_target": None,
            "attempt_number": 1,
            "max_attempts": 3,
            "wait_count": 0,
            "max_waits": 2,
            "elapsed_ms": 10,
        },
        "service": {"status": "available", "latency_ms": None, "last_result": None},
        "messaging": {"queue_size": 0, "redelivered": False},
        "alternatives": {
            "fallback_available": True,
            "fallback_used": False,
            "alternative_targets": ["inventory.fallback"],
        },
        "recent_events": [{"event_type": "TASK_CREATED", "attempt_number": 1, "event_seq": 1}],
    }
    for dotted, value in overrides.items():
        node = state
        *parents, leaf = dotted.split(".")
        for key in parents:
            node = node[key]
        node[leaf] = value
    return SystemState.model_validate(state)


def dispatched(**overrides: Any) -> SystemState:
    """Tarefa já despachada para `inventory.primary`."""
    base = {
        "task.phase": "DISPATCHED",
        "task.current_target": "inventory.primary",
        "task.current_service": "inventory-service",
    }
    return make_state(**{**base, **overrides})


# -- dublês da infraestrutura --------------------------------------------------------


class RecordingPublisher:
    def __init__(self) -> None:
        self.published: list[tuple[dict, str]] = []

    def publish(self, envelope: dict, route) -> None:
        self.published.append((envelope, str(route)))


class RecordingScheduler:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def schedule_timeout_check(self, **kwargs) -> None:
        self.calls.append(("timeout_check", kwargs))

    def schedule_dispatch(self, **kwargs) -> None:
        self.calls.append(("dispatch", kwargs))

    def schedule_reevaluation(self, **kwargs) -> None:
        self.calls.append(("reevaluate", kwargs))


class StaticObserver:
    """Broker com consumidores nas duas rotas e filas vazias, salvo ajuste em `stats`."""

    def __init__(self) -> None:
        self.stats = {
            "inventory.primary": QueueStats(message_count=0, consumer_count=1),
            "inventory.fallback": QueueStats(message_count=0, consumer_count=1),
        }

    def queue_stats(self, route) -> QueueStats:
        return self.stats[str(route)]


@dataclass
class Harness:
    """Camada de coordenação completa com dublês, sobre um `orders.db` temporário."""

    connection: sqlite3.Connection
    directory: Path
    config: ExperimentConfig = field(default_factory=repo_config)
    publisher: RecordingPublisher = field(default_factory=RecordingPublisher)
    scheduler: RecordingScheduler = field(default_factory=RecordingScheduler)
    observer: StaticObserver = field(default_factory=StaticObserver)

    def __post_init__(self) -> None:
        self.executor = DecisionExecutor(self.publisher, self.scheduler, self.config)
        self.states_path = self.directory / "states.jsonl"
        self.decisions_path = self.directory / "decisions.jsonl"
        self.orchestrator = Orchestrator(
            state_builder=StateBuilder(self.config, self.observer),
            engine=RulesDecisionEngine(self.config),
            validator=DecisionValidator(self.config),
            executor=self.executor,
            state_recorder=StateRecorder(JsonlWriter(self.states_path)),
            decision_recorder=DecisionRecorder(JsonlWriter(self.decisions_path)),
        )

    @classmethod
    def with_task(cls, directory: Path) -> "Harness":
        path = directory / "orders.db"
        init_database(path)
        connection = connect(path)
        # Criada agora: um horário fixo no passado estouraria o task_deadline_ms.
        create_order_with_task(connection, execution_id="PILOT_TEST", items_json=ITEMS_JSON, now=utc_now_iso())
        return cls(connection=connection, directory=directory)

    def dispatch(self, task_id: str = "TASK_000001") -> dict:
        """Executa o `CONTINUE` (primeiro despacho) e devolve o envelope publicado."""
        decision = Decision(action=Action.CONTINUE, target="inventory.primary", reason_code=ReasonCode.NORMAL_FLOW)
        self.executor.execute(self.connection, decision, task_id=task_id, decision_id="DEC_TEST")
        return self.publisher.published[-1][0]

    def task(self, task_id: str = "TASK_000001") -> sqlite3.Row:
        return self.connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()

    def order_status(self, task_id: str = "TASK_000001") -> str:
        return self.connection.execute(
            "SELECT o.status FROM orders o JOIN tasks t ON t.order_id = o.order_id WHERE t.task_id = ?",
            (task_id,),
        ).fetchone()[0]

    def trajectory(self, task_id: str = "TASK_000001") -> list[tuple]:
        return [
            tuple(row)
            for row in self.connection.execute(
                "SELECT event_seq, event_type, attempt_number, target, redelivered"
                " FROM task_events WHERE task_id = ? ORDER BY event_id",
                (task_id,),
            )
        ]
