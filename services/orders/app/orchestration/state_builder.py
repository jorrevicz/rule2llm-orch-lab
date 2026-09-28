"""`StateBuilder`: constrói o `SYSTEM_STATE` de um ponto de decisão (RF-013 a RF-015).

Coleta, correlaciona e normaliza sinais em um snapshot (docs/06 §6.2); não escolhe
ação. Rules e LLM recebem exatamente o mesmo snapshot (RNF-001, RNF-012, RNF-019).

Origem de cada sinal:
- tarefa (`task.*`, `service.last_result`): tabela `tasks` do `orders.db`;
- `recent_events`: últimas K ocorrências distintas de `task_events` (repetições da
  mesma mensagem não ocupam a janela; continuam em `task_events.jsonl`);
- `service.latency_ms`: da publicação da última solicitação até a sua resposta (ou até
  agora, se ainda sem resposta); `null` antes do primeiro despacho;
- `messaging.queue_size` e disponibilidade: `queue.declare` passivo no broker;
- `service.status`: `unavailable` se a fila do target não tem consumidor; `degraded`
  se `last_result` é `timeout` ou `transient_error`; senão `available`;
- `alternatives.fallback_available`: `inventory.fallback` tem consumidor (com o
  `inventory-service` inteiro fora do ar, o fallback também não está disponível).
"""

import sqlite3
from collections.abc import Callable
from datetime import datetime

from services.orders.app.db.trajectory import recent_events
from services.orders.app.orchestration.broker_observer import BrokerObserver
from shared.config import ExperimentConfig
from shared.events import EventType
from shared.ids import IdPrefix, unique_id
from shared.messaging import Route
from shared.system_state import (
    AlternativesView,
    MessagingView,
    RecentEvent,
    ServiceView,
    SystemState,
    TaskView,
)
from shared.task import TaskResult
from shared.timestamps import elapsed_ms, parse_iso, to_iso, utc_now

INVENTORY_SERVICE = "inventory-service"
DEGRADED_RESULTS = frozenset({TaskResult.TIMEOUT, TaskResult.TRANSIENT_ERROR})


class StateBuilder:
    def __init__(
        self,
        config: ExperimentConfig,
        observer: BrokerObserver,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._config = config
        self._observer = observer
        self._clock = clock

    def build(self, connection: sqlite3.Connection, task_id: str) -> SystemState:
        now = self._clock()
        task = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if task is None:
            raise ValueError(f"unknown task {task_id}")

        target = Route(task["current_target"]) if task["current_target"] else Route.INVENTORY_PRIMARY
        target_stats = self._observer.queue_stats(target)
        fallback_stats = self._observer.queue_stats(Route.INVENTORY_FALLBACK)
        last_result = TaskResult(task["last_result"]) if task["last_result"] else None
        fallback_available = fallback_stats.consumer_count > 0
        fallback_used = bool(task["fallback_used"])
        window = recent_events(connection, task_id, self._config.context.recent_events_limit)

        return SystemState(
            execution_id=task["execution_id"],
            task_id=task_id,
            state_id=unique_id(IdPrefix.STATE),
            timestamp=to_iso(now),
            current_event_seq=task["current_event_seq"],
            task=TaskView(
                phase=task["status"],
                current_service=INVENTORY_SERVICE if task["current_target"] else None,
                current_target=task["current_target"],
                attempt_number=task["attempt_number"],
                max_attempts=self._config.messaging.max_attempts,
                wait_count=task["wait_count"],
                max_waits=self._config.messaging.max_waits,
                elapsed_ms=round(elapsed_ms(parse_iso(task["created_at"]), now)),
            ),
            service=ServiceView(
                status=_service_status(target_stats.consumer_count, last_result),
                latency_ms=_latency_ms(connection, task_id, now),
                last_result=last_result,
            ),
            messaging=MessagingView(
                queue_size=target_stats.message_count,
                redelivered=_last_event_redelivered(connection, task_id),
            ),
            alternatives=AlternativesView(
                fallback_available=fallback_available,
                fallback_used=fallback_used,
                alternative_targets=(
                    [Route.INVENTORY_FALLBACK.value] if fallback_available and not fallback_used else []
                ),
            ),
            recent_events=[
                RecentEvent(
                    event_type=event.event_type,
                    attempt_number=event.attempt_number,
                    event_seq=event.event_seq,
                )
                for event in window
            ],
        )


def _service_status(consumer_count: int, last_result: TaskResult | None) -> str:
    if consumer_count == 0:
        return "unavailable"
    if last_result in DEGRADED_RESULTS:
        return "degraded"
    return "available"


def _latency_ms(connection: sqlite3.Connection, task_id: str, now: datetime) -> int | None:
    request = connection.execute(
        "SELECT event_id, published_at FROM task_events"
        " WHERE task_id = ? AND event_type = ? AND redelivered = 0"
        " ORDER BY event_id DESC LIMIT 1",
        (task_id, EventType.STOCK_RESERVATION_REQUESTED),
    ).fetchone()
    if request is None:
        return None
    reply = connection.execute(
        "SELECT recorded_at FROM task_events"
        " WHERE task_id = ? AND service = 'inventory' AND redelivered = 0 AND event_id > ?"
        " ORDER BY event_id LIMIT 1",
        (task_id, request["event_id"]),
    ).fetchone()
    end = parse_iso(reply["recorded_at"]) if reply else now
    return max(0, round(elapsed_ms(parse_iso(request["published_at"]), end)))


def _last_event_redelivered(connection: sqlite3.Connection, task_id: str) -> bool:
    row = connection.execute(
        "SELECT redelivered FROM task_events WHERE task_id = ? ORDER BY event_id DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    return bool(row["redelivered"]) if row else False
