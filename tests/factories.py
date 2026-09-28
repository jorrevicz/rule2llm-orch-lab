"""Fábricas de dados de teste."""

from typing import Any

from shared.system_state import SystemState


def make_state(**overrides: Any) -> SystemState:
    """`SYSTEM_STATE` válido de uma tarefa recém-criada; sobrescreva com chaves pontuadas.

    Ex.: make_state(**{"task.phase": "DISPATCHED", "service.last_result": "timeout"})
    """
    state: dict[str, Any] = {
        "execution_id": "PILOT_TEST",
        "task_id": "TASK_000001",
        "state_id": "STATE_TEST",
        "timestamp": "2026-09-28T12:00:00.000Z",
        "current_event_seq": 1,
        "task": {
            "phase": "PENDING",
            "current_service": None,
            "current_target": None,
            "attempt_number": 1,
            "max_attempts": 3,
            "wait_count": 0,
            "max_waits": 2,
            "elapsed_ms": 10,
        },
        "service": {"status": "available", "latency_ms": None, "last_result": None},
        "messaging": {"queue_size": 0, "redelivered": False},
        "alternatives": {
            "fallback_available": True,
            "fallback_used": False,
            "alternative_targets": ["inventory.fallback"],
        },
        "recent_events": [{"event_type": "TASK_CREATED", "attempt_number": 1, "event_seq": 1}],
    }
    for dotted, value in overrides.items():
        node = state
        *parents, leaf = dotted.split(".")
        for key in parents:
            node = node[key]
        node[leaf] = value
    return SystemState.model_validate(state)


def dispatched(**overrides: Any) -> SystemState:
    """Tarefa já despachada para `inventory.primary`."""
    base = {
        "task.phase": "DISPATCHED",
        "task.current_target": "inventory.primary",
        "task.current_service": "inventory-service",
    }
    return make_state(**{**base, **overrides})
