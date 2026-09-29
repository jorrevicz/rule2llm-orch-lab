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
from services.orders.app.db.repositories import (
    abort_task,
    dispatch_context,
    enter_wait,
    mark_retry_dispatched,
    start_fallback,
    start_first_dispatch,
    start_retry,
)
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
        self._retry_delay_ms = config.messaging.retry_delay_ms
        self._wait_delay_ms = config.messaging.wait_delay_ms

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
            Action.RETRY: self._retry,
            Action.WAIT: self._wait,
            Action.FALLBACK: self._fallback,
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

    def _retry(
        self,
        connection: sqlite3.Connection,
        decision: Decision,
        task_id: str,
        decision_id: str,
        effects: list[Effect],
    ) -> ExecutionResult:
        """Nova tentativa lógica no mesmo target, despachada após `retry_delay_ms`.

        `attempt_number + 1` agora; novo `message_id` e novo `event_seq` no despacho
        (`dispatch_scheduled_attempt`). Não é o `autoretry` do Celery (CLAUDE §9).
        """
        now = utc_now_iso()
        attempt_number = start_retry(connection, task_id=task_id, now=now)
        if attempt_number is None:
            raise RuntimeError(f"RETRY is not applicable to task {task_id}")
        record_internal_event(
            connection,
            task_id=task_id,
            event_type=EventType.RETRY_SCHEDULED,
            now=now,
            payload={"decision_id": decision_id, "delay_ms": self._retry_delay_ms},
        )
        effects.append(
            lambda: self._scheduler.schedule_dispatch(
                task_id=task_id, decision_id=decision_id, delay_ms=self._retry_delay_ms
            )
        )
        return ExecutionResult(Action.RETRY, True)

    def _wait(
        self,
        connection: sqlite3.Connection,
        decision: Decision,
        task_id: str,
        decision_id: str,
        effects: list[Effect],
    ) -> ExecutionResult:
        """Não envia nada agora: aguarda `wait_delay_ms` e reavalia o estado.

        `wait_count + 1`; `attempt_number` não muda (CLAUDE §14).
        """
        now = utc_now_iso()
        wait_count = enter_wait(connection, task_id=task_id, now=now)
        if wait_count is None:
            raise RuntimeError(f"WAIT is not applicable to task {task_id}")
        record_internal_event(
            connection,
            task_id=task_id,
            event_type=EventType.WAIT_SCHEDULED,
            now=now,
            payload={"decision_id": decision_id, "delay_ms": self._wait_delay_ms, "wait_count": wait_count},
        )
        effects.append(
            lambda: self._scheduler.schedule_reevaluation(
                task_id=task_id, wait_count=wait_count, delay_ms=self._wait_delay_ms
            )
        )
        return ExecutionResult(Action.WAIT, True)

    def _fallback(
        self,
        connection: sqlite3.Connection,
        decision: Decision,
        task_id: str,
        decision_id: str,
        effects: list[Effect],
    ) -> ExecutionResult:
        """Troca `inventory.primary` por `inventory.fallback` (mesmo inventory-service).

        Publica a solicitação na rota de fallback imediatamente, com novo `message_id` e
        `event_seq`; `attempt_number` não muda e `fallback_used` passa a true (D-15).
        """
        now = utc_now_iso()
        if not start_fallback(connection, task_id=task_id, target=decision.target, now=now):
            raise RuntimeError(f"FALLBACK is not applicable to task {task_id}")
        record_internal_event(
            connection,
            task_id=task_id,
            event_type=EventType.FALLBACK_SCHEDULED,
            now=now,
            payload={"decision_id": decision_id},
        )
        message_id = self._publish_request(connection, task_id, decision_id, now, effects)
        return ExecutionResult(Action.FALLBACK, True, [message_id])

    def dispatch_scheduled_attempt(
        self, connection: sqlite3.Connection, *, task_id: str, decision_id: str
    ) -> bool:
        """Continuação do `RETRY`: publica a solicitação da nova tentativa.

        Nada acontece se a tarefa não está mais em RETRYING (ex.: já concluída por uma
        resposta atrasada, ou despacho já feito numa entrega anterior deste agendamento).
        """
        effects: list[Effect] = []
        with transaction(connection):
            now = utc_now_iso()
            if not mark_retry_dispatched(connection, task_id=task_id, now=now):
                return False
            self._publish_request(connection, task_id, decision_id, now, effects)
        for effect in effects:
            effect()
        return True

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
