"""Orchestrator: conduz um ponto de decisão (docs/06 §6.1, §6.7; docs/07 §7.2).

    StateBuilder → SYSTEM_STATE → DecisionEngine → DecisionValidator
      → resolve_action → decisions.jsonl → DecisionExecutor

O mesmo fluxo vale para Rules e LLM; só o motor muda (RNF-001, RNF-002).
`decision_time_ms` é medido do `SYSTEM_STATE` disponível ao motor até a ação válida
e executável após a validação; a construção do estado fica fora (metodologia §4.5).
"""

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass

from services.orders.app.observability.recorders import (
    DecisionRecord,
    DecisionRecorder,
    StateRecorder,
    ValidationEntry,
)
from services.orders.app.orchestration.decision_engine import DecisionEngine
from services.orders.app.orchestration.executor import DecisionExecutor, ExecutionResult
from services.orders.app.orchestration.state_builder import StateBuilder
from services.orders.app.orchestration.validator import DecisionValidator
from shared.decision import Decision, ValidationResult, resolve_action
from shared.ids import IdPrefix, unique_id
from shared.timestamps import utc_now_iso


@dataclass(frozen=True)
class DecisionOutcome:
    decision_id: str
    state_id: str
    validation: ValidationResult
    executed: Decision
    execution: ExecutionResult


class Orchestrator:
    def __init__(
        self,
        *,
        state_builder: StateBuilder,
        engine: DecisionEngine,
        validator: DecisionValidator,
        executor: DecisionExecutor,
        state_recorder: StateRecorder,
        decision_recorder: DecisionRecorder,
        timer: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._state_builder = state_builder
        self._engine = engine
        self._validator = validator
        self._executor = executor
        self._state_recorder = state_recorder
        self._decision_recorder = decision_recorder
        self._timer = timer

    @property
    def engine_name(self) -> str:
        return self._engine.name

    def handle_decision_point(self, connection: sqlite3.Connection, task_id: str) -> DecisionOutcome:
        state = self._state_builder.build(connection, task_id)
        self._state_recorder.record(state)

        started = self._timer()
        output = self._engine.decide(state)
        validation = self._validator.validate(output.proposal, state)
        executed = resolve_action(output.proposal, validation)
        decision_time_ms = (self._timer() - started) * 1000

        decision_id = unique_id(IdPrefix.DECISION)
        self._decision_recorder.record(
            DecisionRecord(
                execution_id=state.execution_id,
                task_id=task_id,
                state_id=state.state_id,
                decision_id=decision_id,
                decision_engine=self._engine.name,
                proposed_decision=output.proposal,
                validation=ValidationEntry(
                    valid=validation.valid,
                    error=None if validation.error is None else str(validation.error),
                ),
                executed_decision=executed,
                decision_time_ms=round(decision_time_ms, 3),
                llm_inference_ms=output.llm_inference_ms,
                token_usage=output.token_usage,
                timestamp=utc_now_iso(),
            )
        )
        execution = self._executor.execute(connection, executed, task_id=task_id, decision_id=decision_id)
        return DecisionOutcome(decision_id, state.state_id, validation, executed, execution)
