import json

import pytest
from pydantic import ValidationError

from services.orders.app.observability.recorders import DecisionRecord, DecisionRecorder
from shared.artifacts import JsonlWriter

# Metodologia, Código 8 (RULES) — com reason_code também na decisão executada.
RULES_RECORD = {
    "execution_id": "EXP_0042",
    "task_id": "TASK_0187",
    "state_id": "STATE_0091",
    "decision_id": "DEC_0091",
    "decision_engine": "RULES",
    "proposed_decision": {"action": "RETRY", "target": "inventory.primary", "reason_code": "TRANSIENT_RETRY"},
    "validation": {"valid": True, "error": None},
    "executed_decision": {"action": "RETRY", "target": "inventory.primary", "reason_code": "TRANSIENT_RETRY"},
    "decision_time_ms": 1.7,
    "llm_inference_ms": None,
    "token_usage": None,
    "timestamp": "2026-09-10T14:32:12.196Z",
}

# Metodologia, Código 9 — proposta inválida do LLM e ABORT executado.
INVALID_LLM_RECORD = {
    **RULES_RECORD,
    "decision_engine": "LLM",
    "proposed_decision": {"action": "RETRY", "target": "service_c", "reason_code": "RETRY"},
    "validation": {"valid": False, "error": "UNKNOWN_TARGET"},
    "executed_decision": {"action": "ABORT", "target": None, "reason_code": "INVALID_DECISION"},
    "decision_time_ms": 684,
    "llm_inference_ms": 642,
    "token_usage": {"input_tokens": 428, "output_tokens": 21, "total_tokens": 449},
}


def test_rules_record_from_the_methodology_is_valid():
    assert DecisionRecord.model_validate(RULES_RECORD).decision_engine == "RULES"


def test_invalid_llm_proposal_is_a_valid_record():
    record = DecisionRecord.model_validate(INVALID_LLM_RECORD)

    assert record.validation.valid is False
    assert record.executed_decision.reason_code == "INVALID_DECISION"


def test_unreadable_llm_output_is_recorded_without_proposal():
    record = DecisionRecord.model_validate(
        {**INVALID_LLM_RECORD, "proposed_decision": None, "validation": {"valid": False, "error": "MALFORMED_OUTPUT"}}
    )

    assert record.proposed_decision is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"llm_inference_ms": 10},
        {"token_usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}},
    ],
    ids=["inference-time", "tokens"],
)
def test_llm_only_fields_must_be_null_for_rules(overrides):
    with pytest.raises(ValidationError, match="must be null for RULES"):
        DecisionRecord.model_validate({**RULES_RECORD, **overrides})


@pytest.mark.parametrize(
    "overrides",
    [
        {"decision_engine": "rules"},
        {"decision_time_ms": -1},
        {"timestamp": "2026-09-10 14:32:12"},
        {"decision_id": "0091"},
        {"executed_decision": {"action": "ABORT", "target": None}},
        {"extra": "field"},
    ],
    ids=["lowercase-engine", "negative-time", "timestamp", "decision-id", "executed-without-reason", "extra"],
)
def test_invalid_records_are_rejected(overrides):
    with pytest.raises(ValidationError):
        DecisionRecord.model_validate({**RULES_RECORD, **overrides})


def test_recorder_writes_one_line_per_decision(tmp_path):
    path = tmp_path / "decisions.orders-worker.jsonl"
    recorder = DecisionRecorder(JsonlWriter(path))

    recorder.record(DecisionRecord.model_validate(RULES_RECORD))
    recorder.record(DecisionRecord.model_validate(INVALID_LLM_RECORD))

    first, second = (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    assert first == RULES_RECORD
    assert second["executed_decision"]["action"] == "ABORT"
