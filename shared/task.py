"""Estados e resultados da tarefa (docs/05, docs/06 §6.2.1).

Ficam em `shared/` porque fazem parte do contrato do `SYSTEM_STATE`, o mesmo para
Rules e LLM (D-06: `phase` = estado da tarefa).
"""

from enum import StrEnum


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    DISPATCHED = "DISPATCHED"
    PROCESSING = "PROCESSING"
    WAITING = "WAITING"
    RETRYING = "RETRYING"
    FALLBACK_PROCESSING = "FALLBACK_PROCESSING"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"
    DEAD_LETTERED = "DEAD_LETTERED"


TERMINAL_TASK_STATUSES = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.ABORTED, TaskStatus.DEAD_LETTERED}
)


class TaskResult(StrEnum):
    """Valores de `service.last_result` / `tasks.last_result`."""

    OK = "ok"
    TIMEOUT = "timeout"
    TRANSIENT_ERROR = "transient_error"
    INVALID_DATA = "invalid_data"
    FALLBACK_FAILED = "fallback_failed"
