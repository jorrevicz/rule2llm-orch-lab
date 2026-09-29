import json

import pytest

from services.orders.app.llm.llm_engine import LLMDecisionEngine
from services.orders.app.llm.ollama_client import Generation, LLMRuntimeError, LLMTimeout
from services.orders.app.llm.prompt_builder import PromptBuilder
from services.orders.app.orchestration.coordination import build_engine
from services.orders.app.orchestration.orchestrator import Orchestrator
from services.orders.app.orchestration.rules_engine import RulesDecisionEngine
from services.orders.app.settings import Settings
from tests.factories import REPO_CONFIG, Harness, make_state, repo_config

TASK = "TASK_000001"
TEMPLATE = REPO_CONFIG.parent / "prompts" / "decision_prompt_v1.txt"


class FakeClient:
    """Cliente Ollama de teste: devolve um texto fixo ou levanta a exceção dada."""

    def __init__(self, text: str = "", error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> Generation:
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return Generation(
            text=self.text,
            done_reason="stop",
            prompt_eval_count=600,
            eval_count=25,
            wall_ms=420.0,
            total_duration_ms=410.0,
            load_duration_ms=5.0,
        )


def _engine(client: FakeClient) -> LLMDecisionEngine:
    return LLMDecisionEngine(client, PromptBuilder.from_file(TEMPLATE))


CONTINUE_JSON = '{"action": "CONTINUE", "target": "inventory.primary", "reason_code": "NORMAL_FLOW"}'


def test_engine_is_identified_as_llm():
    assert _engine(FakeClient()).name == "LLM"


def test_valid_output_becomes_a_proposal_with_llm_costs():
    output = _engine(FakeClient(CONTINUE_JSON)).decide(make_state())

    assert output.proposal.model_dump() == json.loads(CONTINUE_JSON)
    assert output.failure is None
    assert output.llm_inference_ms == 420.0
    assert output.token_usage == {"input_tokens": 600, "output_tokens": 25, "total_tokens": 625}
    assert output.raw_response == CONTINUE_JSON


def test_prompt_carries_the_same_state_given_to_rules():
    client = FakeClient(CONTINUE_JSON)
    state = make_state()

    _engine(client).decide(state)

    assert json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=2) in client.prompts[0]


def test_malformed_output_has_no_proposal():
    output = _engine(FakeClient("Vou tentar de novo: RETRY")).decide(make_state())

    assert output.proposal is None
    assert output.failure == "MALFORMED_OUTPUT:INVALID_JSON"
    assert output.raw_response == "Vou tentar de novo: RETRY"


def test_inference_timeout_is_a_decision_timeout():
    output = _engine(FakeClient(error=LLMTimeout("timed out"))).decide(make_state())

    assert output.proposal is None
    assert output.failure == "LLM_DECISION_TIMEOUT"
    assert output.llm_inference_ms is not None


def test_runtime_failure_is_not_hidden():
    # Falha da bancada: não vira decisão; sobe para a DLQ e invalida a execução.
    with pytest.raises(LLMRuntimeError):
        _engine(FakeClient(error=LLMRuntimeError("connection refused"))).decide(make_state())


# -- ciclo completo pelo Orchestrator, com o mesmo Validator e Executor ---------------


@pytest.fixture
def harness(tmp_path) -> Harness:
    harness = Harness.with_task(tmp_path)
    yield harness
    harness.connection.close()


def _decide_with(harness: Harness, client: FakeClient) -> dict:
    orchestrator = Orchestrator(
        state_builder=harness.orchestrator._state_builder,
        engine=_engine(client),
        validator=harness.orchestrator._validator,
        executor=harness.executor,
        state_recorder=harness.orchestrator._state_recorder,
        decision_recorder=harness.orchestrator._decision_recorder,
    )
    orchestrator.handle_decision_point(harness.connection, TASK)
    return json.loads(harness.decisions_path.read_text().splitlines()[-1])


def test_valid_llm_decision_is_executed_like_rules(harness):
    record = _decide_with(harness, FakeClient(CONTINUE_JSON))

    assert record["decision_engine"] == "LLM"
    assert record["validation"] == {"valid": True, "error": None}
    assert record["executed_decision"] == json.loads(CONTINUE_JSON)
    assert record["token_usage"] == {"input_tokens": 600, "output_tokens": 25, "total_tokens": 625}
    assert harness.task()["status"] == "DISPATCHED"


def test_invented_target_is_aborted_as_invalid_decision(harness):
    # Metodologia, Código 9.
    record = _decide_with(harness, FakeClient('{"action": "RETRY", "target": "service_c", "reason_code": "RETRY"}'))

    assert record["validation"] == {"valid": False, "error": "UNKNOWN_TARGET"}
    assert record["executed_decision"] == {"action": "ABORT", "target": None, "reason_code": "INVALID_DECISION"}
    assert harness.task()["status"] == "ABORTED"


def test_malformed_output_is_aborted_as_invalid_decision(harness):
    record = _decide_with(harness, FakeClient('{"action": "CONTINUE", "why": "fluxo normal"}'))

    assert record["proposed_decision"] is None
    assert record["validation"] == {"valid": False, "error": "MALFORMED_DECISION"}
    assert record["executed_decision"]["reason_code"] == "INVALID_DECISION"


def test_inference_timeout_is_aborted_as_llm_decision_timeout(harness):
    record = _decide_with(harness, FakeClient(error=LLMTimeout("timed out")))

    assert record["proposed_decision"] is None
    assert record["validation"] == {"valid": False, "error": "LLM_DECISION_TIMEOUT"}
    assert record["executed_decision"] == {"action": "ABORT", "target": None, "reason_code": "LLM_DECISION_TIMEOUT"}
    assert record["llm_inference_ms"] is not None
    assert harness.task()["status"] == "ABORTED"


def test_engine_is_selected_by_configuration(tmp_path):
    settings = Settings(
        broker_url="memory://",
        database_path=tmp_path / "orders.db",
        execution_id="PILOT_TEST",
        data_root=tmp_path,
        service_role="orders-worker",
        ollama_base_url="http://ollama.invalid",
    )
    config = repo_config()
    as_llm = config.model_copy(update={"experiment": config.experiment.model_copy(update={"decision_engine": "LLM"})})
    as_rules = config.model_copy(update={"experiment": config.experiment.model_copy(update={"decision_engine": "RULES"})})

    assert isinstance(build_engine(settings, as_rules), RulesDecisionEngine)
    llm = build_engine(settings, as_llm)
    assert isinstance(llm, LLMDecisionEngine)
