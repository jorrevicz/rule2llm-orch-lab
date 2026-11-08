"""Reavaliação offline dos mesmos estados (D-34; docs/15 §15.6)."""

import json

from scripts.analysis.replay_decisions import (
    SourceState,
    replay_one,
    select_states,
    stratum,
    summarise,
    valid_executions,
    write_outputs,
)
from services.orders.app.orchestration.decision_engine import LLM_DECISION_TIMEOUT, EngineOutput
from shared.decision import ProposedDecision
from tests.factories import make_state, repo_config

CONFIG = repo_config()


def source(state_id: str, **service) -> SourceState:
    dumped = make_state().model_dump(mode="json")
    dumped["service"].update(service)
    return SourceState("PILOT_0001", state_id, dumped, CONFIG)


class Fixed:
    def __init__(self, output: EngineOutput) -> None:
        self.output = output

    def decide(self, state):
        return self.output


def _proposal(action: str, target=None) -> EngineOutput:
    return EngineOutput(proposal=ProposedDecision(action=action, target=target, reason_code="X"))


def test_selection_is_deterministic_and_capped_per_stratum():
    states = [source(f"STATE_{n}") for n in range(30)] + [source("STATE_X", last_result="invalid_data")]

    first = select_states(states, 5, seed=7)
    second = select_states(list(reversed(states)), 5, seed=7)

    assert [s.state_id for group in first.values() for s in group] == [s.state_id for group in second.values() for s in group]
    assert sorted(len(group) for group in first.values()) == [1, 5]
    assert first != select_states(states, 5, seed=8)


def test_stratum_ignores_identifiers_and_elapsed_time():
    one, other = source("STATE_1"), source("STATE_2")
    other.state["task"]["elapsed_ms"] = 999
    other.state["execution_id"] = "PILOT_0002"

    assert stratum(one.state, CONFIG) == stratum(other.state, CONFIG)


def test_both_engines_are_judged_on_the_same_state_with_the_same_validator():
    row = replay_one(source("STATE_1"), "A8", Fixed(_proposal("WAIT")))

    assert row["rules"]["action"] == "CONTINUE" and row["rules"]["opportunity"] == "ADEQUATE"
    assert row["llm"]["action"] == "WAIT" and row["llm"]["opportunity"] == "INADEQUATE"
    assert row["llm"]["valid"] is True  # válido no Validator, inadequado na rubrica


def test_timeout_and_unreadable_output_have_no_proposal():
    timeout = replay_one(source("S"), "A8", Fixed(EngineOutput(proposal=None, failure=LLM_DECISION_TIMEOUT)))

    assert timeout["llm"]["opportunity"] == "NO_DECISION"
    assert timeout["llm"]["validation_error"] == LLM_DECISION_TIMEOUT


def test_summary_reports_agreement_and_rates_by_stratum_and_total():
    rows = [
        replay_one(source("S1"), "A8", Fixed(_proposal("CONTINUE"))),
        replay_one(source("S2"), "A8", Fixed(_proposal("WAIT"))),
    ]

    by_name = {row["stratum"]: row for row in summarise(rows)}

    assert by_name["TOTAL"]["states"] == 2
    assert by_name["A8"]["agreement_rate"] == 0.5
    assert by_name["A8"]["rules_opportunity_rate"] == 1.0
    assert by_name["A8"]["llm_opportunity_rate"] == 0.5
    assert json.loads(by_name["A8"]["llm_actions"]) == {"CONTINUE": 1, "WAIT": 1}


def test_only_valid_executions_of_the_phase_are_used(tmp_path):
    def execution(name, **metadata):
        directory = tmp_path / "experiment" / name
        directory.mkdir(parents=True)
        (directory / "execution_metadata.json").write_text(json.dumps(metadata))

    execution("EXP_0001", run_status="VALID", phase="EXPERIMENT", eligible_for_sample=True)
    execution("EXP_0002", run_status="INVALID", phase="EXPERIMENT", eligible_for_sample=True)
    execution("EXP_0003", run_status="VALID", phase="EXPERIMENT", eligible_for_sample=False)
    (tmp_path / "experiment" / "replay").mkdir()

    assert [d.name for d in valid_executions(tmp_path, "experiment")] == ["EXP_0001"]


def test_outputs_are_marked_as_outside_the_sample(tmp_path):
    rows = [replay_one(source("S1"), "A8", Fixed(_proposal("CONTINUE")))]

    write_outputs(tmp_path / "out", rows, summarise(rows), {"eligible_for_sample": False})

    assert json.loads((tmp_path / "out" / "replay_metadata.json").read_text())["eligible_for_sample"] is False
    assert len((tmp_path / "out" / "replay_decisions.jsonl").read_text().splitlines()) == 1
