"""`DecisionValidator`: validação determinística comum a Rules e LLM (docs/06 §6.9; RF-028).

Um único validador para as duas abordagens (RNF-009). Não corrige a decisão: só diz
se ela é válida e, se não for, qual o erro. As regras seguem o Código 6 da
metodologia / piloto §18, na mesma ordem, com três acréscimos 🔬 (ver doc 06):

- `CONTINUE` só antes do primeiro despacho (`CONTINUE_AFTER_DISPATCH`): depois dele, as
  ações admissíveis são `RETRY`, `FALLBACK`, `WAIT` e `ABORT`. O destino do `CONTINUE`
  é o do fluxo (`inventory.primary`): o target pode vir nulo ou `inventory.primary`;
  qualquer outro é `INVALID_CONTINUE_TARGET` (D-18 — o `SYSTEM_STATE` de uma tarefa
  nova não traz a rota primária, então exigi-la puniria o LLM por falta de informação);
- `RETRY` em `inventory.fallback` é inválido (`FALLBACK_RETRY_LIMIT`, D-15), pois
  `fallback_max_attempts = 1`;
- limites operacionais valem para qualquer motor (D-19), como o de tentativas: `WAIT`
  com `wait_count >= max_waits` é `WAIT_LIMIT_EXCEEDED`, e qualquer ação exceto `ABORT`
  com `elapsed_ms >= task_deadline_ms` é `TASK_DEADLINE_EXCEEDED`. Sem isso, um motor
  que não respeite os limites deixaria a tarefa sem fim (visto na bancada do LLM).
"""

from shared.config import ExperimentConfig
from shared.decision import Action, ProposedDecision, ValidationErrorCode, ValidationResult
from shared.messaging import Route
from shared.system_state import SystemState
from shared.task import TERMINAL_TASK_STATUSES

ALLOWED_ACTIONS = frozenset(action.value for action in Action)
ALLOWED_TARGETS = frozenset({None, Route.INVENTORY_PRIMARY.value, Route.INVENTORY_FALLBACK.value})
PRIMARY = Route.INVENTORY_PRIMARY.value
FALLBACK = Route.INVENTORY_FALLBACK.value


class DecisionValidator:
    def __init__(self, config: ExperimentConfig) -> None:
        if config.messaging.fallback_max_attempts != 1:
            # O SYSTEM_STATE não carrega contador de tentativas no fallback; valores
            # maiores exigiriam estender o contrato (D-15).
            raise ValueError("only fallback_max_attempts = 1 is supported (D-15)")
        self._task_deadline_ms = config.messaging.task_deadline_ms

    def validate(
        self, proposal: ProposedDecision | None, state: SystemState, *, timed_out: bool = False
    ) -> ValidationResult:
        """`timed_out`: o motor não chegou a produzir decisão (inferência > timeout)."""
        if timed_out:
            return ValidationResult.invalid(ValidationErrorCode.LLM_DECISION_TIMEOUT)
        error = self._first_error(proposal, state)
        return ValidationResult.ok() if error is None else ValidationResult.invalid(error)

    def _first_error(
        self, proposal: ProposedDecision | None, state: SystemState
    ) -> ValidationErrorCode | None:
        if proposal is None:
            return ValidationErrorCode.MALFORMED_DECISION
        if proposal.action not in ALLOWED_ACTIONS:
            return ValidationErrorCode.UNKNOWN_ACTION
        if proposal.target not in ALLOWED_TARGETS:
            return ValidationErrorCode.UNKNOWN_TARGET
        if not proposal.reason_code:
            return ValidationErrorCode.MISSING_REASON_CODE

        task, alternatives = state.task, state.alternatives
        action = Action(proposal.action)

        if task.elapsed_ms >= self._task_deadline_ms and action != Action.ABORT:  # D-19
            return ValidationErrorCode.TASK_DEADLINE_EXCEEDED

        if action == Action.CONTINUE:
            if proposal.target not in (None, PRIMARY):  # D-18
                return ValidationErrorCode.INVALID_CONTINUE_TARGET
            if task.current_target is not None:
                return ValidationErrorCode.CONTINUE_AFTER_DISPATCH

        if action == Action.RETRY:
            if task.attempt_number >= task.max_attempts:
                return ValidationErrorCode.RETRY_LIMIT_EXCEEDED
            # O RETRY repete a tentativa corrente: exige tarefa já despachada e o mesmo
            # target (antes do 1º despacho, target e current_target são ambos nulos).
            if proposal.target is None or proposal.target != task.current_target:
                return ValidationErrorCode.INVALID_RETRY_TARGET
            if proposal.target == FALLBACK:
                return ValidationErrorCode.FALLBACK_RETRY_LIMIT

        if action == Action.FALLBACK:
            if not alternatives.fallback_available:
                return ValidationErrorCode.FALLBACK_NOT_AVAILABLE
            if alternatives.fallback_used:
                return ValidationErrorCode.FALLBACK_ALREADY_USED
            if proposal.target != FALLBACK:
                return ValidationErrorCode.INVALID_FALLBACK_TARGET

        if action in {Action.WAIT, Action.ABORT} and proposal.target is not None:
            return ValidationErrorCode.TARGET_NOT_ALLOWED

        if action == Action.WAIT and task.wait_count >= task.max_waits:  # D-19
            return ValidationErrorCode.WAIT_LIMIT_EXCEEDED

        if task.phase in TERMINAL_TASK_STATUSES:
            return ValidationErrorCode.TERMINAL_TASK

        return None
