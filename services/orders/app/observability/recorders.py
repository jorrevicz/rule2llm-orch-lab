"""Gravação dos artefatos de decisão do orders-service (docs/10 §10.3).

Cada processo grava o seu próprio arquivo (`states.<writer>.jsonl`,
`decisions.<writer>.jsonl`); a consolidação é feita por
`scripts/pilot/collect_artifacts.py`.
"""

from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from shared.artifacts import Artifact, JsonlWriter, shard_path
from shared.envelope import ISO_UTC_MS_PATTERN, DecisionId, ExecutionId, TaskId
from shared.system_state import StateId, SystemState


class StateRecorder:
    """`states.jsonl`: o que exatamente o decisor sabia naquele instante (RF-031)."""

    def __init__(self, writer: JsonlWriter) -> None:
        self._writer = writer

    @classmethod
    def for_process(cls, artifacts_dir: Path, writer_name: str) -> "StateRecorder":
        return cls(JsonlWriter(shard_path(artifacts_dir, Artifact.STATES, writer_name)))

    def record(self, state: SystemState) -> None:
        snapshot = state.model_dump(mode="json")
        self._writer.write(
            {
                "execution_id": state.execution_id,
                "task_id": state.task_id,
                "state_id": state.state_id,
                "timestamp": state.timestamp,
                "current_event_seq": state.current_event_seq,
                "system_state": snapshot,
                "recent_events": snapshot["recent_events"],
            }
        )


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProposedDecision(_Record):
    """O que o motor propôs, como foi lido. Pode estar incompleto (ex.: LLM sem
    `reason_code`); a validação é do `DecisionValidator`, não do registro."""

    action: str | None
    target: str | None
    reason_code: str | None


class ExecutedDecision(_Record):
    """A ação efetivamente executada; sempre completa."""

    action: str
    target: str | None
    reason_code: str


class ValidationEntry(_Record):
    valid: StrictBool
    error: str | None


class TokenUsage(_Record):
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


class DecisionRecord(_Record):
    """Uma linha de `decisions.jsonl` (metodologia Códigos 7–9; RF-033, RNF-008).

    - `decision_time_ms`: do `SYSTEM_STATE` disponível ao motor até a ação válida e
      executável após a validação comum (sem a construção do estado).
    - `llm_inference_ms` e `token_usage`: somente para `LLM`; nulos para `RULES`.
    - `proposed_decision`: nulo quando a saída do motor nem pôde ser lida como decisão.
    - `executed_decision.reason_code`: presente sempre, para distinguir, por exemplo,
      `ABORT / INVALID_DECISION` de um `ABORT` proposto pelo próprio motor.
    """

    execution_id: ExecutionId
    task_id: TaskId
    state_id: StateId
    decision_id: DecisionId
    decision_engine: Literal["RULES", "LLM"]
    proposed_decision: ProposedDecision | None
    validation: ValidationEntry
    executed_decision: ExecutedDecision
    decision_time_ms: Annotated[float, Field(ge=0)]
    llm_inference_ms: Annotated[float, Field(ge=0)] | None
    token_usage: TokenUsage | None
    timestamp: Annotated[str, Field(pattern=ISO_UTC_MS_PATTERN)]

    @model_validator(mode="after")
    def _llm_fields_only_for_llm(self) -> Self:
        if self.decision_engine == "RULES" and (
            self.llm_inference_ms is not None or self.token_usage is not None
        ):
            raise ValueError("llm_inference_ms and token_usage must be null for RULES")
        return self


class DecisionRecorder:
    """`decisions.jsonl`: o que foi proposto, validado e executado (RF-033)."""

    def __init__(self, writer: JsonlWriter) -> None:
        self._writer = writer

    @classmethod
    def for_process(cls, artifacts_dir: Path, writer_name: str) -> "DecisionRecorder":
        return cls(JsonlWriter(shard_path(artifacts_dir, Artifact.DECISIONS, writer_name)))

    def record(self, record: DecisionRecord) -> None:
        self._writer.write(record.model_dump(mode="json"))
