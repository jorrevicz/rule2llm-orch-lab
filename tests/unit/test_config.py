from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from shared.config import CONFIG_PATH_ENV, load_experiment_config, resolve_config_path

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "experiment_config.yml"

# Chaves do piloto §31 + task_deadline_ms (§14.1) + decision_engine.
EXPECTED_KEYS = {
    "experiment": {"phase", "decision_engine", "repetitions", "execution_order"},
    "messaging": {
        "inventory_timeout_ms",
        "max_attempts",
        "retry_delay_ms",
        "wait_delay_ms",
        "max_waits",
        "fallback_max_attempts",
        "queue_high_watermark",
        "task_deadline_ms",
    },
    "context": {"recent_events_limit"},
    "llm": {
        "runtime",
        "runtime_version",
        "model",
        "model_digest",
        "quantization",
        "temperature",
        "top_p",
        "max_tokens",
        "seed",
        "session_memory",
        "request_timeout_seconds",
        "stream",
        "response_format",
        "num_ctx",
        "keep_alive",
    },
    "workload": {"dataset", "requests", "rate_per_second", "seed"},
    "fault": {"type", "seed"},
}


def _raw_repo_config() -> dict:
    return yaml.safe_load(REPO_CONFIG.read_text(encoding="utf-8"))


def _write_config(tmp_path: Path, raw: dict) -> Path:
    path = tmp_path / "experiment_config.yml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_repo_config_has_exactly_the_expected_keys():
    raw = _raw_repo_config()

    assert {section: set(values) for section, values in raw.items()} == EXPECTED_KEYS


def test_repo_config_loads_as_pilot():
    config = load_experiment_config(REPO_CONFIG)

    assert config.experiment.phase == "pilot"
    assert config.experiment.decision_engine in {"RULES", "LLM"}
    assert config.llm.session_memory is False


def test_unknown_key_is_rejected(tmp_path):
    raw = _raw_repo_config()
    raw["messaging"]["max_retries"] = 3

    with pytest.raises(ValidationError):
        load_experiment_config(_write_config(tmp_path, raw))


@pytest.mark.parametrize("engine", ["airflow", "rules", "llm"])
def test_invalid_decision_engine_is_rejected(tmp_path, engine):
    raw = _raw_repo_config()
    raw["experiment"]["decision_engine"] = engine  # D-06: somente RULES / LLM

    with pytest.raises(ValidationError):
        load_experiment_config(_write_config(tmp_path, raw))


def test_missing_key_is_rejected(tmp_path):
    raw = _raw_repo_config()
    del raw["messaging"]["task_deadline_ms"]

    with pytest.raises(ValidationError):
        load_experiment_config(_write_config(tmp_path, raw))


def test_config_path_comes_from_environment(monkeypatch, tmp_path):
    custom = tmp_path / "custom.yml"
    monkeypatch.setenv(CONFIG_PATH_ENV, str(custom))

    assert resolve_config_path() == custom


def test_explicit_path_overrides_environment(monkeypatch, tmp_path):
    monkeypatch.setenv(CONFIG_PATH_ENV, str(tmp_path / "from_env.yml"))
    explicit = tmp_path / "explicit.yml"

    assert resolve_config_path(explicit) == explicit
