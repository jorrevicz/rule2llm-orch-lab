import json
import threading
from pathlib import Path

import pytest

from scripts.pilot.new_execution import METADATA_FILE, next_pilot_id, open_execution
from shared.artifacts import Artifact, JsonlWriter, execution_dir, shard_path

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
