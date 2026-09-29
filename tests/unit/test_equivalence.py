"""Equivalência entre abordagens (CLAUDE §3, §35; RNF-001, RNF-009, RNF-010, RNF-012).

Troca-se só o motor. Para a mesma proposta, o mesmo estado produz a mesma validação,
a mesma ação executada, os mesmos efeitos e o mesmo registro — exceto o nome do motor
e os campos exclusivos do LLM.
"""

import json

import pytest

from services.orders.app.llm.llm_engine import LLMDecisionEngine
from services.orders.app.llm.ollama_client import Generation
from services.orders.app.llm.prompt_builder import PromptBuilder
from services.orders.app.orchestration.decision_engine import EngineOutput
from services.orders.app.orchestration.orchestrator import Orchestrator
from services.orders.app.orchestration.rules_engine import RulesDecisionEngine
from tests.factories import Harness

TASK = "TASK_000001"


class MirrorEngine:
    """Motor "LLM" de teste que propõe exatamente o que o Rules proporia."""

    name = "LLM"

    def __init__(self, config) -> None:
        self._rules = RulesDecisionEngine(config)
        self.seen_states = []

    def decide(self, state) -> EngineOutput:
        self.seen_states.append(state)
        return EngineOutput(proposal=self._rules.decide(state).proposal, llm_inference_ms=5.0)


def _run(tmp_path, name: str, engine_factory):
    directory = tmp_path / name
    directory.mkdir()
    harness = Harness.with_task(directory)
    engine = engine_factory(harness.config)
    orchestrator = Orchestrator(
        state_builder=harness.orchestrator._state_builder,
        engine=engine,
        validator=harness.orchestrator._validator,
        executor=harness.executor,
        state_recorder=harness.orchestrator._state_recorder,
        decision_recorder=harness.orchestrator._decision_recorder,
    )
    outcome = orchestrator.handle_decision_point(harness.connection, TASK)
    decision = json.loads(harness.decisions_path.read_text().splitlines()[-1])
    state = json.loads(harness.states_path.read_text().splitlines()[-1])
    return harness, outcome, decision, state


@pytest.fixture
def runs(tmp_path):
    rules = _run(tmp_path, "rules", RulesDecisionEngine)
    llm = _run(tmp_path, "llm", MirrorEngine)
    yield rules, llm
    rules[0].connection.close()
    llm[0].connection.close()


def test_same_state_reaches_both_engines(runs):
    (_, _, _, rules_state), (_, _, _, llm_state) = runs

    def normalized(record):
        snapshot = dict(record["system_state"])
        for volatile in ("state_id", "timestamp"):
            snapshot.pop(volatile)
        snapshot["task"] = {**snapshot["task"], "elapsed_ms": 0}
        return snapshot

    assert normalized(rules_state) == normalized(llm_state)


def test_same_proposal_gets_the_same_validation_and_execution(runs):
    (rules_h, rules_out, _, _), (llm_h, llm_out, _, _) = runs

    assert rules_out.validation == llm_out.validation
    assert rules_out.executed == llm_out.executed
    assert rules_h.trajectory() == llm_h.trajectory()
    assert [route for _, route in rules_h.publisher.published] == [route for _, route in llm_h.publisher.published]
    assert [call[0] for call in rules_h.scheduler.calls] == [call[0] for call in llm_h.scheduler.calls]


def test_records_differ_only_in_engine_specific_fields(runs):
    (_, _, rules_rec, _), (_, _, llm_rec, _) = runs
    engine_specific = {"decision_engine", "llm_inference_ms", "token_usage"}
    volatile = {"state_id", "decision_id", "timestamp", "decision_time_ms"}

    assert set(rules_rec) == set(llm_rec)
    assert {k: v for k, v in rules_rec.items() if k not in engine_specific | volatile} == {
        k: v for k, v in llm_rec.items() if k not in engine_specific | volatile
    }
    assert (rules_rec["decision_engine"], llm_rec["decision_engine"]) == ("RULES", "LLM")
    assert rules_rec["llm_inference_ms"] is None and llm_rec["llm_inference_ms"] == 5.0


# -- com o LLMDecisionEngine real (cliente Ollama falso) -----------------------------


class RulesMirroringClient:
    """Responde, via "Ollama", exatamente o JSON que o Rules decidiria para o estado."""

    def __init__(self, config, builder: PromptBuilder) -> None:
        self._rules = RulesDecisionEngine(config)
        self._builder = builder
        self.state = None

    def generate(self, prompt: str) -> Generation:
        text = json.dumps(self._rules.policy(self.state).model_dump(mode="json"))
        return Generation(text, "stop", 500, 20, 800.0, 790.0, 1.0)


class RealLLMEngine(LLMDecisionEngine):
    def __init__(self, config) -> None:
        builder = PromptBuilder.from_file(config.llm.prompt_template)
        self.client = RulesMirroringClient(config, builder)
        super().__init__(self.client, builder)

    def decide(self, state):
        self.client.state = state
        return super().decide(state)


@pytest.fixture
def real_llm_runs(tmp_path):
    rules = _run(tmp_path, "rules-real", RulesDecisionEngine)
    llm = _run(tmp_path, "llm-real", RealLLMEngine)
    yield rules, llm
    rules[0].connection.close()
    llm[0].connection.close()


def test_real_llm_engine_goes_through_the_same_validation_and_execution(real_llm_runs):
    (rules_h, rules_out, rules_rec, _), (llm_h, llm_out, llm_rec, _) = real_llm_runs

    assert rules_out.validation == llm_out.validation
    assert rules_out.executed == llm_out.executed
    assert rules_h.trajectory() == llm_h.trajectory()
    assert llm_rec["decision_engine"] == "LLM"
    assert llm_rec["llm_inference_ms"] == 800.0
    assert llm_rec["token_usage"] == {"input_tokens": 500, "output_tokens": 20, "total_tokens": 520}
    assert rules_rec["token_usage"] is None
