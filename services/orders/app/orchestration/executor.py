"""`DecisionExecutor`: executa uma decisão já validada (docs/06 §6.10; RNF-010).

Comum a Rules e LLM. Não decide nem reinterpreta: recebe uma `Decision` executável
(já passada por `resolve_action`) e aplica a semântica da ação. Cada ação roda numa
transação (estado da tarefa + trajetória); os efeitos externos (publicar mensagem,
agendar tarefa interna) só acontecem depois do commit.
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field

from services.orders.app.db.connection import transaction
from services.orders.app.db.repositories import abort_task
from services.orders.app.db.trajectory import record_internal_event
from shared.decision import Action, Decision
from shared.events import EventType
from shared.timestamps import utc_now_iso

Effect = Callable[[], None]


@dataclass
class ExecutionResult:
    action: Action
    changed_state: bool
    published_message_ids: list[str] = field(default_factory=list)


class DecisionExecutor:
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
        return {Action.ABORT: self._abort}

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
