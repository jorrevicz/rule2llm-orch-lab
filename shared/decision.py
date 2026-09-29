"""Contrato da decisão (docs/06 §6.4, §6.9–6.11; glossário §12.4).

Rules e LLM retornam o mesmo schema: `{"action", "target", "reason_code"}`.

- `ProposedDecision`: o que o motor propôs, como foi lido — pode trazer qualquer
  texto (um LLM pode inventar ação ou target); quem decide se é válido é o
  `DecisionValidator`, comum às duas abordagens.
- `Decision`: decisão executável, com ação e target do espaço permitido. É o que o
  `DecisionExecutor` recebe; exportado para `contracts/decision.schema.json`.
- `resolve_action`: válida → a própria proposta; inválida → `ABORT / INVALID_DECISION`
  (sem autocorreção, sem fallback para Rules — CLAUDE §23).
"""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

from shared.envelope import Target


class Action(StrEnum):
    CONTINUE = "CONTINUE"
    RETRY = "RETRY"
    WAIT = "WAIT"
    FALLBACK = "FALLBACK"
    ABORT = "ABORT"


class ReasonCode(StrEnum):
    """Códigos usados pelo `RulesDecisionEngine` e pelo executor/orquestrador.

    O validador exige `reason_code` não vazio (Código 6 da metodologia); não restringe
    a proposta a este catálogo.
    """

    NORMAL_FLOW = "NORMAL_FLOW"
    TRANSIENT_RETRY = "TRANSIENT_RETRY"
    PRIMARY_EXHAUSTED = "PRIMARY_EXHAUSTED"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    SERVICE_UNAVAILABLE_LIMIT = "SERVICE_UNAVAILABLE_LIMIT"
    QUEUE_PRESSURE = "QUEUE_PRESSURE"
    QUEUE_PRESSURE_LIMIT = "QUEUE_PRESSURE_LIMIT"
    ATTEMPTS_EXHAUSTED = "ATTEMPTS_EXHAUSTED"
    FALLBACK_FAILED = "FALLBACK_FAILED"
    INVALID_DATA = "INVALID_DATA"
    TASK_DEADLINE_EXCEEDED = "TASK_DEADLINE_EXCEEDED"
    UNMAPPED_STATE = "UNMAPPED_STATE"
    # Executor / orquestrador
    INVALID_DECISION = "INVALID_DECISION"
    LLM_DECISION_TIMEOUT = "LLM_DECISION_TIMEOUT"


class ValidationErrorCode(StrEnum):
    UNKNOWN_ACTION = "UNKNOWN_ACTION"
    UNKNOWN_TARGET = "UNKNOWN_TARGET"
    MISSING_REASON_CODE = "MISSING_REASON_CODE"
    INVALID_CONTINUE_TARGET = "INVALID_CONTINUE_TARGET"
    CONTINUE_AFTER_DISPATCH = "CONTINUE_AFTER_DISPATCH"
    RETRY_LIMIT_EXCEEDED = "RETRY_LIMIT_EXCEEDED"
    INVALID_RETRY_TARGET = "INVALID_RETRY_TARGET"
    FALLBACK_RETRY_LIMIT = "FALLBACK_RETRY_LIMIT"
    FALLBACK_NOT_AVAILABLE = "FALLBACK_NOT_AVAILABLE"
    FALLBACK_ALREADY_USED = "FALLBACK_ALREADY_USED"
    INVALID_FALLBACK_TARGET = "INVALID_FALLBACK_TARGET"
    TARGET_NOT_ALLOWED = "TARGET_NOT_ALLOWED"
    WAIT_LIMIT_EXCEEDED = "WAIT_LIMIT_EXCEEDED"          # D-19
    TASK_DEADLINE_EXCEEDED = "TASK_DEADLINE_EXCEEDED"    # D-19
    TERMINAL_TASK = "TERMINAL_TASK"
    MALFORMED_DECISION = "MALFORMED_DECISION"  # saída do motor não legível como decisão
    LLM_DECISION_TIMEOUT = "LLM_DECISION_TIMEOUT"  # inferência excedeu o timeout (CLAUDE §25)


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProposedDecision(_Contract):
    action: str | None
    target: str | None
    reason_code: str | None


class Decision(_Contract):
    action: Action
    target: Target | None
    reason_code: Annotated[StrictStr, Field(min_length=1)]

    @model_validator(mode="after")
    def _target_matches_action(self) -> "Decision":
        # Formato; as regras dependentes do estado ficam no DecisionValidator.
        # CONTINUE: o destino é o do fluxo (inventory.primary); o target pode vir nulo (D-18).
        if self.action == Action.CONTINUE:
            if self.target not in (None, "inventory.primary"):
                raise ValueError("CONTINUE only goes to inventory.primary")
            return self
        needs_target = self.action in {Action.RETRY, Action.FALLBACK}
        if needs_target != (self.target is not None):
            raise ValueError(f"{self.action} {'requires' if needs_target else 'forbids'} a target")
        return self


class ValidationResult(_Contract):
    valid: bool
    error: ValidationErrorCode | None = None

    @classmethod
    def ok(cls) -> "ValidationResult":
        return cls(valid=True)

    @classmethod
    def invalid(cls, error: ValidationErrorCode) -> "ValidationResult":
        return cls(valid=False, error=error)


INVALID_DECISION_ABORT = Decision(
    action=Action.ABORT, target=None, reason_code=ReasonCode.INVALID_DECISION
)
LLM_TIMEOUT_ABORT = Decision(
    action=Action.ABORT, target=None, reason_code=ReasonCode.LLM_DECISION_TIMEOUT
)


def resolve_action(proposal: ProposedDecision | None, validation: ValidationResult) -> Decision:
    """Decisão válida → executa a proposta; inválida → `ABORT / INVALID_DECISION`.

    Sem decisão porque a inferência excedeu o timeout → `ABORT / LLM_DECISION_TIMEOUT`.
    """
    if validation.error == ValidationErrorCode.LLM_DECISION_TIMEOUT:
        return LLM_TIMEOUT_ABORT
    if not validation.valid or proposal is None:
        return INVALID_DECISION_ABORT
    return Decision.model_validate(proposal.model_dump())
