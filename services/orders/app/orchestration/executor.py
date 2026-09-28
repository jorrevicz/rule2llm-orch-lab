"""`DecisionExecutor`: executa uma decisão já validada (docs/06 §6.3, §6.10; RNF-010).

Comum a Rules e LLM. Não decide nem reinterpreta: recebe uma `Decision` executável
(já passada por `resolve_action`) e aplica a semântica da ação. Cada ação roda numa
transação (estado da tarefa + trajetória); os efeitos externos (publicar mensagem,
agendar tarefa interna) só acontecem depois do commit.
"""

import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field

from services.orders.app.db.connection import transaction
from services.orders.app.db.repositories import abort_task, dispatch_context, start_first_dispatch
from services.orders.app.db.trajectory import (
    EventSource,
    advance_event_seq,
    record_internal_event,
    record_message,
)
from services.orders.app.messaging.publisher import CommandPublisher
from services.orders.app.orchestration.scheduler import TaskScheduler
from shared.config import ExperimentConfig
from shared.decision import Action, Decision
from shared.envelope import MessageEnvelope, build_envelope
from shared.events import EventType
from shared.messaging import Route
from shared.timestamps import utc_now_iso

Effect = Callable[[], None]


@dataclass
class ExecutionResult:
    action: Action
    changed_state: bool
    published_message_ids: list[str] = field(default_factory=list)


class DecisionExecutor:
    def __init__(
        self, publisher: CommandPublisher, scheduler: TaskScheduler, config: ExperimentConfig
    ) -> None:
        self._publisher = publisher
        self._scheduler = scheduler
        self._inventory_timeout_ms = config.messaging.inventory_timeout_ms

    def execute(
        self, connection: sqlite3.Connection, decision: Decision, *, task_id: str, decision_id: str
    ) -> ExecutionResult:
        handler = self._handlers().get(decision.action)
        if handler is None:
            raise RuntimeError(f"validated decision is not executable: {decision.action}")
        effects: list[Effect] = []
        with transaction(connection):
            result = handler(connection, decision, task_id, decision_id, effects)
        for effect in effects:
            effect()
        return result

    def _handlers(self) -> dict[Action, Callable[..., ExecutionResult]]:
        return {
            Action.CONTINUE: self._continue,
            Action.ABORT: self._abort,
        }

    # -- ações --------------------------------------------------------------------

    def _continue(
        self,
        connection: sqlite3.Connection,
        decision: Decision,
        task_id: str,
        decision_id: str,
        effects: list[Effect],
    ) -> ExecutionResult:
        """Próxima transição normal: o primeiro despacho para `inventory.primary`."""
        now = utc_now_iso()
        if not start_first_dispatch(connection, task_id=task_id, target=decision.target, now=now):
            raise RuntimeError(f"CONTINUE is not applicable to task {task_id}")
        message_id = self._publish_request(connection, task_id, decision_id, now, effects)
        return ExecutionResult(Action.CONTINUE, True, [message_id])

    def _abort(
        self,
        connection: sqlite3.Connection,
        decision: Decision,
        task_id: str,
        decision_id: str,
        effects: list[Effect],
    ) -> ExecutionResult:
        now = utc_now_iso()
        changed = abort_task(connection, task_id=task_id, now=now)
        if changed:
            record_internal_event(
                connection,
                task_id=task_id,
                event_type=EventType.TASK_ABORTED,
                now=now,
                payload={"decision_id": decision_id, "reason_code": decision.reason_code},
            )
        return ExecutionResult(action=Action.ABORT, changed_state=changed)

    # -- passos comuns -----------------------------------------------------------------

    def _publish_request(
        self,
        connection: sqlite3.Connection,
        task_id: str,
        decision_id: str,
        now: str,
        effects: list[Effect],
    ) -> str:
        """Solicitação de reserva da tentativa corrente (target e `attempt_number` da tarefa).

        Registra `STOCK_RESERVATION_REQUESTED` na trajetória e agenda, para depois do
        commit, a publicação e a verificação de timeout operacional (RF-023).
        """
        context = dispatch_context(connection, task_id)
        event_seq = advance_event_seq(connection, task_id=task_id, now=now)
        envelope = build_envelope(
            execution_id=context.execution_id,
            task_id=task_id,
            event_type=EventType.STOCK_RESERVATION_REQUESTED,
            event_seq=event_seq,
            attempt_number=context.attempt_number,
            decision_id=decision_id,
            target=context.target,
            payload={"order_id": context.order_id, "items": json.loads(context.items_json)},
        )
        record_message(
            connection,
            MessageEnvelope.model_validate(envelope),
            event_seq=event_seq,
            service=EventSource.ORDERS,
            now=now,
        )
        route = Route(context.target)
        message_id = envelope["message_id"]
        effects.append(lambda: self._publisher.publish(envelope, route))
        effects.append(
            lambda: self._scheduler.schedule_timeout_check(
                task_id=task_id, request_message_id=message_id, delay_ms=self._inventory_timeout_ms
            )
        )
        return message_id
