"""Contrato do `SYSTEM_STATE` (docs/06 §6.2, D-06).

Snapshot normalizado apresentado ao mecanismo decisório. Rules e LLM recebem
exatamente o mesmo objeto (RNF-001, RNF-012). Exportado para
`contracts/system_state.schema.json` por `scripts/contracts/export_schemas.py`.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from shared.envelope import ISO_UTC_MS_PATTERN, ExecutionId, Target, TaskId
from shared.events import EventType
from shared.task import TaskResult, TaskStatus

StateId = Annotated[StrictStr, Field(pattern=r"^STATE_[A-Za-z0-9]+$")]
NonNegativeInt = Annotated[StrictInt, Field(ge=0)]
PositiveInt = Annotated[StrictInt, Field(ge=1)]


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TaskView(_Contract):
    phase: TaskStatus  # D-06: estado da tarefa
    current_service: Literal["inventory-service"] | None
    current_target: Target | None
    attempt_number: PositiveInt
    max_attempts: PositiveInt
    wait_count: NonNegativeInt
    max_waits: NonNegativeInt
    elapsed_ms: NonNegativeInt


class ServiceView(_Contract):
    status: Literal["available", "degraded", "unavailable"]
    latency_ms: NonNegativeInt | None
    last_result: TaskResult | None


class MessagingView(_Contract):
    queue_size: NonNegativeInt
    redelivered: StrictBool


class AlternativesView(_Contract):
    fallback_available: StrictBool
    fallback_used: StrictBool
    alternative_targets: list[Target]


class RecentEvent(_Contract):
    event_type: EventType
    attempt_number: PositiveInt
    event_seq: PositiveInt


class SystemState(_Contract):
    execution_id: ExecutionId
    task_id: TaskId
    state_id: StateId
    timestamp: Annotated[StrictStr, Field(pattern=ISO_UTC_MS_PATTERN)]
    current_event_seq: PositiveInt
    task: TaskView
    service: ServiceView
    messaging: MessagingView
    alternatives: AlternativesView
    recent_events: list[RecentEvent]  # janela K (recent_events_limit), ordem crescente
