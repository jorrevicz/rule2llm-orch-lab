"""Rubrica de adequação da decisão (D-33; docs/15 §15.5)."""

from copy import deepcopy
from itertools import product

import pytest

from scripts.analysis.decision_adequacy import (
    ADEQUATE,
    INADEQUATE,
    NO_DECISION,
    NOT_DISPATCHED,
    UNDETERMINED,
    adequate_actions,
    classify_opportunity,
    dispatch_result,
    evaluate_decisions,
)
from services.orders.app.orchestration.rules_engine import RulesDecisionEngine
from shared.system_state import SystemState
from tests.factories import make_state, repo_config

CONFIG = repo_config()
WATERMARK = CONFIG.messaging.queue_high_watermark


def state(**changes) -> dict:
    base = {
        "task": {"phase": "PENDING", "attempt_number": 1, "max_attempts": 3, "wait_count": 0, "max_waits": 2, "elapsed_ms": 10},
        "service": {"status": "available", "last_result": None},
        "messaging": {"queue_size": 0},
        "alternatives": {"fallback_available": True, "fallback_used": False},
    }
    result = deepcopy(base)
    for path, value in changes.items():
        section, field = path.split("__")
        result[section][field] = value
    return result


@pytest.mark.parametrize(
    ("changes", "rule", "expected"),
    [
        ({"task__elapsed_ms": 60000}, "A1", {"ABORT"}),
        ({"service__last_result": "invalid_data"}, "A2", {"ABORT"}),
        ({"service__last_result": "fallback_failed"}, "A3", {"ABORT"}),
        ({"service__status": "unavailable"}, "A4", {"WAIT", "ABORT"}),
        ({"service__status": "unavailable", "task__wait_count": 2}, "A4", {"ABORT"}),
        ({"service__last_result": "timeout", "messaging__queue_size": WATERMARK}, "A5", {"WAIT", "FALLBACK"}),
        ({"service__last_result": "timeout", "messaging__queue_size": WATERMARK, "task__wait_count": 2}, "A5", {"FALLBACK", "ABORT"}),
        ({"service__last_result": "transient_error"}, "A6", {"RETRY", "FALLBACK"}),
        ({"service__last_result": "timeout", "task__attempt_number": 3}, "A6", {"FALLBACK"}),
        (
            {"service__last_result": "timeout", "task__attempt_number": 3, "alternatives__fallback_used": True},
            "A6",
            {"ABORT"},
        ),
        ({"messaging__queue_size": WATERMARK}, "A7", {"CONTINUE", "WAIT", "FALLBACK"}),
        ({"messaging__queue_size": WATERMARK, "task__wait_count": 2}, "A7", {"CONTINUE", "FALLBACK", "ABORT"}),
        ({}, "A8", {"CONTINUE"}),
    ],
)
def test_each_rubric_rule(changes, rule, expected):
    assert adequate_actions(state(**changes), CONFIG) == (rule, frozenset(expected))


def test_states_outside_the_rubric_are_undetermined():
    assert adequate_actions(state(task__phase="DISPATCHED"), CONFIG) == ("A9", None)
    assert classify_opportunity(state(task__phase="DISPATCHED"), "CONTINUE", CONFIG)[2] == UNDETERMINED


def test_rubric_is_not_the_rules_policy():
    # O Rules insiste na rota primária; o FALLBACK antecipado também é adequado.
    failed = state(service__last_result="transient_error")
    assert classify_opportunity(failed, "FALLBACK", CONFIG)[2] == ADEQUATE
    assert classify_opportunity(failed, "RETRY", CONFIG)[2] == ADEQUATE
    assert classify_opportunity(failed, "ABORT", CONFIG)[2] == INADEQUATE


def test_missing_proposal_and_unknown_action():
    assert classify_opportunity(state(), None, CONFIG)[2] == NO_DECISION
    assert classify_opportunity(state(), "continue", CONFIG)[2] == INADEQUATE
    assert classify_opportunity(None, "CONTINUE", CONFIG)[2] == UNDETERMINED


def test_every_rules_decision_falls_inside_the_adequate_set():
    # Limite declarado em docs/15 §15.5: por construção, o eixo de oportunidade não mede superioridade.
    engine = RulesDecisionEngine(CONFIG)
    grid = product(
        [None, "ok", "timeout", "transient_error", "invalid_data", "fallback_failed"],
        ["available", "degraded", "unavailable"],
        [0, WATERMARK],
        [1, 3],
        [0, 2],
        [False, True],
        ["PENDING", "WAITING"],
    )
    for last_result, status, queue, attempt, waits, fallback_used, phase in grid:
        dumped = make_state().model_dump(mode="json")
        dumped["task"].update(
            phase=phase, attempt_number=attempt, wait_count=waits, max_attempts=3, max_waits=2,
            current_service="inventory-service", current_target="inventory.primary",
        )
        dumped["service"].update(status=status, last_result=last_result)
        dumped["messaging"]["queue_size"] = queue
        dumped["alternatives"].update(
            fallback_used=fallback_used, alternative_targets=[] if fallback_used else ["inventory.fallback"]
        )
        proposal = engine.decide(SystemState.model_validate(dumped)).proposal
        assert classify_opportunity(dumped, proposal.action, CONFIG)[2] == ADEQUATE, dumped


def _event(seq, kind, target="inventory.primary", attempt=1):
    return {"event_seq": seq, "event_type": kind, "target": target, "attempt_number": attempt}


def test_dispatch_result_uses_the_first_response_to_that_attempt():
    # Sequência real (PILOT_0060): timeout da 1ª tentativa e sucesso tardio depois do fallback.
    events = [
        _event(1, "TASK_CREATED"),
        _event(2, "STOCK_RESERVATION_REQUESTED"),
        _event(3, "INVENTORY_TIMEOUT"),
        _event(4, "RETRY_SCHEDULED", attempt=2),
        _event(5, "STOCK_RESERVATION_REQUESTED", attempt=2),
        _event(6, "STOCK_RESERVATION_FAILED", attempt=2),
        _event(10, "FALLBACK_SCHEDULED", "inventory.fallback", 2),
        _event(11, "STOCK_RESERVATION_REQUESTED", "inventory.fallback", 2),
        _event(12, "STOCK_RESERVATION_SUCCEEDED", "inventory.fallback", 2),
        _event(14, "STOCK_RESERVATION_SUCCEEDED"),
    ]
    assert dispatch_result("CONTINUE", 1, events) == "TIMEOUT"
    assert dispatch_result("RETRY", 3, events) == "FAILED"
    assert dispatch_result("FALLBACK", 9, events) == "SUCCEEDED"
    assert dispatch_result("WAIT", 3, events) == NOT_DISPATCHED
    assert dispatch_result("ABORT", 3, events) == NOT_DISPATCHED


def test_evaluate_decisions_summarises_the_three_axes():
    states = [
        {"state_id": "S1", "current_event_seq": 1, "system_state": state()},
        {"state_id": "S2", "current_event_seq": 3, "system_state": state(service__last_result="invalid_data")},
    ]
    events = [
        {**_event(1, "TASK_CREATED"), "task_id": "T1"},
        {**_event(2, "STOCK_RESERVATION_REQUESTED"), "task_id": "T1"},
        {**_event(3, "STOCK_RESERVATION_FAILED"), "task_id": "T1"},
    ]
    decisions = [
        {
            "task_id": "T1", "state_id": "S1", "proposed_decision": {"action": "CONTINUE"},
            "validation": {"valid": True, "error": None}, "executed_decision": {"action": "CONTINUE"},
        },
        {
            "task_id": "T1", "state_id": "S2", "proposed_decision": {"action": "RETRY"},
            "validation": {"valid": False, "error": "X"}, "executed_decision": {"action": "ABORT"},
        },
        {
            "task_id": "T1", "state_id": "S2", "proposed_decision": None,
            "validation": {"valid": False, "error": "LLM_DECISION_TIMEOUT"}, "executed_decision": {"action": "ABORT"},
        },
    ]

    rows, summary = evaluate_decisions(decisions, states, events, [{"task_id": "T1", "status": "ABORTED"}], CONFIG)

    assert [row["opportunity"] for row in rows] == [ADEQUATE, INADEQUATE, NO_DECISION]
    assert rows[0]["dispatch_result"] == "FAILED" and rows[1]["dispatch_result"] == NOT_DISPATCHED
    assert rows[0]["task_final_status"] == "ABORTED"
    assert summary == {
        "decision_valid_rate": 0.3333,
        "decision_opportunity_rate": 0.5,
        "decisions_without_proposal": 1,
        "decisions_undetermined": 0,
        "dispatch_success_rate": 0.0,
    }
