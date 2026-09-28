import copy
import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from scripts.contracts.export_schemas import SYSTEM_STATE_SCHEMA_PATH, render, system_state_schema
from shared.system_state import SystemState

DOC_06 = Path(__file__).resolve().parents[2] / "docs" / "06-modelo-de-decisao.md"


def _doc_06_example() -> dict:
    """O exemplo de SYSTEM_STATE do doc 06 §6.2 precisa ser válido pelo contrato."""
    text = DOC_06.read_text(encoding="utf-8")
    section = text[text.index("## 6.2 `SYSTEM_STATE`"):]
    block = re.search(r"```json\n(.*?)```", section, re.DOTALL).group(1)
    return json.loads(block)


def test_committed_schema_matches_the_code():
    # Se falhar: rode `python -m scripts.contracts.export_schemas` e revise o impacto.
    assert SYSTEM_STATE_SCHEMA_PATH.read_text(encoding="utf-8") == render(system_state_schema())


def test_documented_example_is_valid():
    state = SystemState.model_validate(_doc_06_example())

    assert state.task.phase == "RETRYING"
    assert state.alternatives.alternative_targets == ["inventory.fallback"]


def test_schema_is_nested_as_decided_in_d06():
    schema = json.loads(SYSTEM_STATE_SCHEMA_PATH.read_text(encoding="utf-8"))

    assert set(schema["required"]) == {
        "execution_id",
        "task_id",
        "state_id",
        "timestamp",
        "current_event_seq",
        "task",
        "service",
        "messaging",
        "alternatives",
        "recent_events",
    }
    assert schema["additionalProperties"] is False


def _with(path: str, value) -> dict:
    state = copy.deepcopy(_doc_06_example())
    *parents, leaf = path.split(".")
    node = state
    for key in parents:
        node = node[key]
    node[leaf] = value
    return state


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("task.phase", "READY"),  # D-06: somente estados da tarefa
        ("task.phase", "RECOVERED"),
        ("task.current_target", "service_c"),
        ("task.attempt_number", 0),
        ("task.elapsed_ms", -1),
        ("service.status", "down"),
        ("service.last_result", "boom"),
        ("messaging.queue_size", "4"),
        ("messaging.redelivered", "false"),
        ("alternatives.alternative_targets", ["service_c"]),
        ("state_id", "0091"),
        ("timestamp", "2026-08-27 12:00:02"),
        ("observed_latency_ms", 2054),  # forma plana antiga (D-06)
    ],
)
def test_invalid_state_is_rejected(path, value):
    with pytest.raises(ValidationError):
        SystemState.model_validate(_with(path, value))


def test_nullable_fields_accept_null_before_any_reply():
    state = _with("service.latency_ms", None)
    state["service"]["last_result"] = None
    state["task"]["current_target"] = None
    state["task"]["current_service"] = None

    assert SystemState.model_validate(state).service.latency_ms is None
