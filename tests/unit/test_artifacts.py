import json
import threading
from pathlib import Path

import pytest

from scripts.pilot.llm_readiness import LLMReadiness
from scripts.pilot.new_execution import (
    EFFECTIVE_CONFIG_FILE,
    METADATA_FILE,
    SCENARIOS_DIR,
    available_scenarios,
    next_pilot_id,
    open_execution,
)
from shared.artifacts import Artifact, JsonlWriter, execution_dir, shard_path
from shared.config import load_experiment_config, load_scenario

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "experiment_config.yml"


def test_pilot_and_experiment_executions_never_share_a_directory(tmp_path):
    assert execution_dir(tmp_path, "PILOT_0001") == tmp_path / "pilot" / "PILOT_0001"
    assert execution_dir(tmp_path, "EXP_0001") == tmp_path / "experiment" / "EXP_0001"


@pytest.mark.parametrize("execution_id", ["RUN_0001", "0001", "PILOT", ""])
def test_unknown_execution_prefix_is_rejected(tmp_path, execution_id):
    with pytest.raises(ValueError):
        execution_dir(tmp_path, execution_id)


def test_each_writer_has_its_own_shard(tmp_path):
    assert shard_path(tmp_path, Artifact.STATES, "orders-api").name == "states.orders-api.jsonl"


def test_jsonl_writer_keeps_one_record_per_line_under_concurrency(tmp_path):
    writer = JsonlWriter(tmp_path / "exec" / "states.orders-api.jsonl")

    threads = [
        threading.Thread(target=lambda n=n: [writer.write({"n": n, "i": i}) for i in range(200)])
        for n in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    lines = writer.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1600
    assert all(json.loads(line).keys() == {"n", "i"} for line in lines)


def test_pilot_ids_are_sequential_and_skip_the_development_execution(tmp_path):
    assert next_pilot_id(tmp_path) == "PILOT_0001"

    (tmp_path / "pilot" / "PILOT_0000").mkdir(parents=True)
    (tmp_path / "pilot" / "PILOT_0007").mkdir()

    assert next_pilot_id(tmp_path) == "PILOT_0008"


def test_open_execution_writes_pilot_metadata(tmp_path):
    directory = open_execution(tmp_path, "normal", REPO_CONFIG)

    metadata = json.loads((directory / METADATA_FILE).read_text(encoding="utf-8"))
    assert directory == tmp_path / "pilot" / "PILOT_0001"
    assert metadata["execution_id"] == "PILOT_0001"
    assert metadata["phase"] == "PILOT"
    assert metadata["eligible_for_sample"] is False
    assert metadata["decision_engine"] in {"RULES", "LLM"}
    assert metadata["context_policy"]["recent_events_limit"] >= 1
    assert len(metadata["experiment_config_hash"]) == 64
    assert metadata["message_ordering_policy"]["logical_clock"] is False
    assert metadata["llm"]["prompt_template"] == "config/prompts/decision_prompt_v1.txt"
    assert len(metadata["llm"]["prompt_template_hash"]) == 64
    # Preenchidos pelo protocolo de execução (M7), nunca inventados aqui.
    for field in ("run_status", "readiness_status", "invalid_reason", "finished_at"):
        assert metadata[field] is None


def test_open_execution_refuses_a_non_pilot_config(tmp_path):
    config = tmp_path / "experiment_config.yml"
    config.write_text(
        REPO_CONFIG.read_text(encoding="utf-8").replace("phase: pilot", "phase: experiment"),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit):
        open_execution(tmp_path / "data", "normal", config)
    assert not (tmp_path / "data").exists()


# -- motor por execução e readiness do LLM (M5-T06) ---------------------------------


def _readiness(status: str = "PASS") -> LLMReadiness:
    return LLMReadiness(
        status=status,
        runtime_version="0.0.0-test",
        model="llama3.1:8b",
        model_digest="abc123",
        quantization="Q4_K_M",
        model_load_ms=1000.0,
        warmup_inference_ms=2000.0,
        failures=[] if status == "PASS" else ["model not available"],
    )


def test_engine_is_chosen_per_execution_without_touching_the_base_config(tmp_path):
    base_before = REPO_CONFIG.read_text(encoding="utf-8")

    directory = open_execution(tmp_path, "normal", REPO_CONFIG, engine="LLM", llm_readiness=lambda _: _readiness())

    effective = load_experiment_config(directory / EFFECTIVE_CONFIG_FILE)
    metadata = json.loads((directory / METADATA_FILE).read_text(encoding="utf-8"))
    assert effective.experiment.decision_engine == "LLM"
    assert metadata["decision_engine"] == "LLM"
    assert REPO_CONFIG.read_text(encoding="utf-8") == base_before
    assert metadata["base_experiment_config_hash"] != metadata["experiment_config_hash"]


def test_llm_execution_records_what_the_runtime_reported(tmp_path):
    directory = open_execution(tmp_path, "normal", REPO_CONFIG, engine="LLM", llm_readiness=lambda _: _readiness())

    metadata = json.loads((directory / METADATA_FILE).read_text(encoding="utf-8"))
    assert metadata["readiness_status"] == "PASS"
    assert metadata["software"]["llm_runtime_version"] == "0.0.0-test"
    assert (metadata["llm"]["model_digest"], metadata["llm"]["quantization"]) == ("abc123", "Q4_K_M")
    assert (metadata["model_load_ms"], metadata["warmup_inference_ms"]) == (1000.0, 2000.0)
    assert metadata["run_status"] is None


def test_failed_llm_readiness_makes_the_execution_invalid(tmp_path):
    directory = open_execution(tmp_path, "normal", REPO_CONFIG, engine="LLM", llm_readiness=lambda _: _readiness("FAIL"))

    metadata = json.loads((directory / METADATA_FILE).read_text(encoding="utf-8"))
    assert metadata["readiness_status"] == "FAIL"
    assert metadata["run_status"] == "INVALID"
    assert "model not available" in metadata["invalid_reason"]


def test_rules_execution_does_not_touch_the_llm_runtime(tmp_path):
    def forbidden(_):
        raise AssertionError("Rules execution must not call the LLM runtime")

    directory = open_execution(tmp_path, "normal", REPO_CONFIG, engine="RULES", llm_readiness=forbidden)

    metadata = json.loads((directory / METADATA_FILE).read_text(encoding="utf-8"))
    assert metadata["decision_engine"] == "RULES"
    assert metadata["readiness_status"] is None


# -- cenários (M6-T06) ---------------------------------------------------------------


def test_scenario_replaces_workload_and_fault_in_the_effective_config(tmp_path):
    directory = open_execution(tmp_path, "timeout", REPO_CONFIG, engine="RULES")

    effective = load_experiment_config(directory / EFFECTIVE_CONFIG_FILE)
    scenario = load_scenario(SCENARIOS_DIR / "timeout.yml")
    metadata = json.loads((directory / METADATA_FILE).read_text(encoding="utf-8"))
    assert effective.fault == scenario.fault and effective.workload == scenario.workload
    assert effective.messaging == load_experiment_config(REPO_CONFIG).messaging  # resto intacto
    assert metadata["scenario_id"] == "timeout"
    assert metadata["scenario_config"] == "config/scenarios/timeout.yml"
    assert len(metadata["scenario_config_hash"]) == 64
    assert metadata["seeds"] == {"workload": 1042, "fault": scenario.fault.seed, "llm": effective.llm.seed}
    assert metadata["fault"]["delay_ms"] == scenario.fault.delay_ms


def test_unknown_scenario_is_refused_before_allocating_an_execution(tmp_path):
    with pytest.raises(SystemExit):
        open_execution(tmp_path, "not_a_scenario", REPO_CONFIG)
    assert not (tmp_path / "pilot").exists()


def test_the_six_methodology_scenarios_exist_and_load():
    names = available_scenarios()

    assert names == sorted(["normal", "overload", "intermittent_failure", "timeout", "inconsistent_data", "recovery"])
    for name in names:
        assert load_scenario(SCENARIOS_DIR / f"{name}.yml").scenario_id == name
