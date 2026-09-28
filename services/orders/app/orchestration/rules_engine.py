"""`RulesDecisionEngine`: linha de base determinística (docs/06 §6.6; RF-024).

Política ordenada e explícita, baseada somente no `SYSTEM_STATE`; a primeira regra
que casa decide. Transcrição da metodologia (Código 4) e do piloto §14.3, com o
ajuste D-06 na regra de fluxo normal (`phase` = estado da tarefa). Congelada antes
da coleta definitiva (RNF-028): não alterar sem atualizar a especificação.
"""

from services.orders.app.orchestration.decision_engine import EngineOutput
from shared.config import ExperimentConfig
from shared.decision import Action, Decision, ProposedDecision, ReasonCode
from shared.messaging import Route
from shared.system_state import SystemState
from shared.task import TaskResult, TaskStatus

TRANSIENT_RESULTS = frozenset({TaskResult.TIMEOUT, TaskResult.TRANSIENT_ERROR})
NORMAL_FLOW_PHASES = frozenset({TaskStatus.PENDING, TaskStatus.WAITING})  # D-06


def _decision(action: Action, target: str | None, reason: ReasonCode) -> Decision:
    return Decision(action=action, target=target, reason_code=reason)


class RulesDecisionEngine:
    name = "RULES"

    def __init__(self, config: ExperimentConfig) -> None:
        self.task_deadline_ms = config.messaging.task_deadline_ms
        self.max_attempts = config.messaging.max_attempts
        self.max_waits = config.messaging.max_waits
        self.queue_high_watermark = config.messaging.queue_high_watermark

    def decide(self, state: SystemState) -> EngineOutput:
        decision = self.policy(state)
        return EngineOutput(proposal=ProposedDecision(**decision.model_dump(mode="json")))

    def policy(self, state: SystemState) -> Decision:
        task, service, messaging, alternatives = (
            state.task,
            state.service,
            state.messaging,
            state.alternatives,
        )

        # 1. deadline da tarefa
        if task.elapsed_ms >= self.task_deadline_ms:
            return _decision(Action.ABORT, None, ReasonCode.TASK_DEADLINE_EXCEEDED)

        # 2. dados inválidos
        if service.last_result == TaskResult.INVALID_DATA:
            return _decision(Action.ABORT, None, ReasonCode.INVALID_DATA)

        # 3. fallback já falhou
        if service.last_result == TaskResult.FALLBACK_FAILED:
            return _decision(Action.ABORT, None, ReasonCode.FALLBACK_FAILED)

        # 4. serviço indisponível → WAIT enquanto houver orçamento; senão ABORT
        if service.status == "unavailable":
            if task.wait_count < self.max_waits:
                return _decision(Action.WAIT, None, ReasonCode.SERVICE_UNAVAILABLE)
            return _decision(Action.ABORT, None, ReasonCode.SERVICE_UNAVAILABLE_LIMIT)

        # 5. pressão de fila
        if messaging.queue_size >= self.queue_high_watermark:
            if task.wait_count < self.max_waits:
                return _decision(Action.WAIT, None, ReasonCode.QUEUE_PRESSURE)
            return _decision(Action.ABORT, None, ReasonCode.QUEUE_PRESSURE_LIMIT)

        # 6. falha transitória / timeout → RETRY → FALLBACK → ABORT
        if service.last_result in TRANSIENT_RESULTS:
            if task.attempt_number < self.max_attempts:
                return _decision(Action.RETRY, task.current_target, ReasonCode.TRANSIENT_RETRY)
            if alternatives.fallback_available and not alternatives.fallback_used:
                return _decision(
                    Action.FALLBACK, Route.INVENTORY_FALLBACK.value, ReasonCode.PRIMARY_EXHAUSTED
                )
            return _decision(Action.ABORT, None, ReasonCode.ATTEMPTS_EXHAUSTED)

        # 7. fluxo normal (D-06: PENDING ou WAITING)
        if task.phase in NORMAL_FLOW_PHASES:
            return _decision(Action.CONTINUE, Route.INVENTORY_PRIMARY.value, ReasonCode.NORMAL_FLOW)

        # 8. estado não mapeado
        return _decision(Action.ABORT, None, ReasonCode.UNMAPPED_STATE)
