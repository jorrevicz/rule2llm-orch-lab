"""Abre uma execução de piloto: aloca o `execution_id`, cria o diretório e grava
`execution_metadata.json` (docs/10 §10.3; metodologia Código 12).

    eval "$(.venv/bin/python -m scripts.pilot.new_execution --engine LLM --scenario timeout)"
    docker compose up -d --build --wait     # usa EXECUTION_ID e EXECUTION_CONFIG_PATH

O motor e o cenário são escolhidos por execução (`--engine RULES|LLM`, `--scenario` =
um arquivo de `config/scenarios/`). A configuração efetiva é gravada no diretório da
execução: config base com `decision_engine` ajustado e as seções `workload` e `fault`
do cenário (M6-T06). Os serviços a leem pelo caminho em `EXECUTION_CONFIG_PATH` (caminho
dentro do container, que o compose repassa como `EXPERIMENT_CONFIG_PATH`; no host,
`EXPERIMENT_CONFIG_PATH` não é alterado). A config base e o cenário versionados não mudam.

Com `--engine LLM`, roda a readiness e o warm-up do Ollama (`llm_readiness`) e registra
versão do runtime, digest, quantização, `model_load_ms` e `warmup_inference_ms`. Se a
readiness reprovar, a execução é registrada como inválida e o script sai com erro.

Somente fase de piloto: `phase = PILOT`, `eligible_for_sample = false`. Campos que
não podem ser obtidos de forma confiável neste momento ficam `null` (nunca
inventados); versões de componentes, readiness e `run_status` são preenchidos pelo
protocolo de execução (M7-T06).
"""

import argparse
import hashlib
import json
import platform
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

import yaml

from scripts.pilot.llm_readiness import LLMReadiness
from scripts.pilot.llm_readiness import check as check_llm_readiness
from shared.artifacts import execution_dir
from shared.config import (
    ExperimentConfig,
    ScenarioFile,
    load_experiment_config,
    load_scenario,
    resolve_config_path,
)
from shared.ids import IdPrefix, sequential_id
from shared.timestamps import utc_now_iso

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = REPO_ROOT / "data"
METADATA_FILE = "execution_metadata.json"
EFFECTIVE_CONFIG_FILE = "experiment_config.yml"
CONTAINER_DATA_ROOT = Path("/srv/data")
SCENARIOS_DIR = REPO_ROOT / "config" / "scenarios"
EXECUTION_ID_WIDTH = 4
_PILOT_ID = re.compile(r"^PILOT_(\d+)$")


def next_pilot_id(data_root: Path) -> str:
    pilot_dir = data_root / "pilot"
    numbers = [
        int(match.group(1))
        for path in (pilot_dir.iterdir() if pilot_dir.exists() else [])
        if (match := _PILOT_ID.match(path.name))
    ]
    return sequential_id(IdPrefix.PILOT_EXECUTION, max(numbers, default=0) + 1, EXECUTION_ID_WIDTH)


def sha256_of(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _command_output(*command: str) -> str | None:
    try:
        completed = subprocess.run(command, capture_output=True, text=True, cwd=REPO_ROOT)
    except FileNotFoundError:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def git_commit() -> str | None:
    """Hash do commit; sufixo "-dirty" se há alterações rastreadas não commitadas."""
    commit = _command_output("git", "rev-parse", "HEAD")
    if commit is None:
        return None
    changes = _command_output("git", "status", "--porcelain", "--untracked-files=no")
    return f"{commit}-dirty" if changes else commit


def hardware() -> dict[str, object]:
    cpu = ram_gb = None
    if platform.system() == "Darwin":
        cpu = _command_output("sysctl", "-n", "machdep.cpu.brand_string")
        memory = _command_output("sysctl", "-n", "hw.memsize")
        ram_gb = round(int(memory) / 1024**3, 1) if memory else None
    return {"cpu": cpu, "ram_gb": ram_gb, "gpu": None}


def _display_path(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path)


def available_scenarios(scenarios_dir: Path = SCENARIOS_DIR) -> list[str]:
    return sorted(path.stem for path in scenarios_dir.glob("*.yml"))


def build_metadata(
    execution_id: str, scenario_id: str, config: ExperimentConfig, config_path: Path
) -> dict[str, object]:
    dataset = REPO_ROOT / config.workload.dataset
    return {
        "execution_id": execution_id,
        "phase": "PILOT",
        "eligible_for_sample": False,
        "scenario_id": scenario_id,
        "repetition_id": None,
        "decision_engine": config.experiment.decision_engine,
        "experiment_version": None,
        "git_commit": git_commit(),
        "experiment_config": _display_path(config_path),
        "experiment_config_hash": sha256_of(config_path),
        "scenario_config": None,
        "scenario_config_hash": None,
        "dataset": config.workload.dataset,
        "dataset_hash": sha256_of(dataset),
        "seeds": {"workload": config.workload.seed, "fault": config.fault.seed, "llm": config.llm.seed},
        "software": {
            "python_version": None,
            "docker_compose_version": _command_output("docker", "compose", "version", "--short"),
            "rabbitmq_version": None,
            "llm_runtime": config.llm.runtime,
            "llm_runtime_version": config.llm.runtime_version,
        },
        "llm": {
            **config.llm.model_dump(exclude={"runtime", "runtime_version"}),
            "prompt_template_hash": sha256_of(REPO_ROOT / config.llm.prompt_template),
        },
        "hardware": hardware(),
        "context_policy": {
            "recent_events_limit": config.context.recent_events_limit,
            "llm_session_memory": config.llm.session_memory,
        },
        "message_ordering_policy": {
            "scope": "per_task",
            "field": "event_seq",
            "global_total_order": False,
            "logical_clock": False,
        },
        "readiness_status": None,
        "model_load_ms": None,
        "warmup_inference_ms": None,
        "started_at": utc_now_iso(),
        "finished_at": None,
        "run_status": None,
        "invalid_reason": None,
    }


def write_effective_config(
    base_path: Path, directory: Path, engine: str | None, scenario: ScenarioFile
) -> Path:
    """Config base + motor da execução + `workload`/`fault` do cenário."""
    raw = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    if engine is not None:
        raw["experiment"]["decision_engine"] = engine
    raw["workload"] = scenario.workload.model_dump(mode="json")
    raw["fault"] = scenario.fault.model_dump(mode="json")
    header = (
        f"# Config efetiva de {directory.name}: {_display_path(base_path)}"
        f" + cenário {scenario.scenario_id} (gerada por new_execution; não editar).\n"
    )
    path = directory / EFFECTIVE_CONFIG_FILE
    path.write_text(header + yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def apply_llm_readiness(metadata: dict, readiness: LLMReadiness) -> None:
    """Registra o que o runtime informou; reprovação invalida a execução (CLAUDE §24)."""
    metadata["software"]["llm_runtime_version"] = readiness.runtime_version
    metadata["llm"]["model_digest"] = readiness.model_digest
    metadata["llm"]["quantization"] = readiness.quantization
    metadata["llm_readiness"] = asdict(readiness)
    metadata["readiness_status"] = readiness.status
    metadata["model_load_ms"] = readiness.model_load_ms
    metadata["warmup_inference_ms"] = readiness.warmup_inference_ms
    if readiness.status != "PASS":
        metadata["run_status"] = "INVALID"
        metadata["invalid_reason"] = "llm_readiness_failed: " + "; ".join(readiness.failures)


def open_execution(
    data_root: Path = DEFAULT_DATA_ROOT,
    scenario_id: str = "normal",
    config_path: Path | None = None,
    *,
    engine: str | None = None,
    llm_readiness: Callable[[ExperimentConfig], LLMReadiness] = check_llm_readiness,
    scenarios_dir: Path = SCENARIOS_DIR,
) -> Path:
    base_path = resolve_config_path(config_path).resolve()
    if load_experiment_config(base_path).experiment.phase != "pilot":
        raise SystemExit("config phase is not 'pilot': this script only opens pilot executions")
    scenario_path = scenarios_dir / f"{scenario_id}.yml"
    if not scenario_path.is_file():
        raise SystemExit(f"unknown scenario {scenario_id!r}; available: {available_scenarios(scenarios_dir)}")
    scenario = load_scenario(scenario_path)
    execution_id = next_pilot_id(data_root)
    directory = execution_dir(data_root, execution_id)
    directory.mkdir(parents=True)
    effective_path = write_effective_config(base_path, directory, engine, scenario)
    config = load_experiment_config(effective_path)

    metadata = build_metadata(execution_id, scenario.scenario_id, config, effective_path)
    metadata["base_experiment_config"] = _display_path(base_path)
    metadata["base_experiment_config_hash"] = sha256_of(base_path)
    metadata["scenario_config"] = _display_path(scenario_path)
    metadata["scenario_config_hash"] = sha256_of(scenario_path)
    metadata["fault"] = config.fault.model_dump(mode="json")
    metadata["workload"] = config.workload.model_dump(mode="json")
    if config.experiment.decision_engine == "LLM":
        apply_llm_readiness(metadata, llm_readiness(config))
    (directory / METADATA_FILE).write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return directory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", default="normal", choices=available_scenarios())
    parser.add_argument("--engine", choices=["RULES", "LLM"], default=None)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    args = parser.parse_args()

    directory = open_execution(args.data_root, args.scenario, engine=args.engine)
    metadata = json.loads((directory / METADATA_FILE).read_text(encoding="utf-8"))
    if metadata["run_status"] == "INVALID":
        print(f"# execução {directory.name} inválida: {metadata['invalid_reason']}", file=sys.stderr)
        return 1
    container_config = CONTAINER_DATA_ROOT / directory.relative_to(args.data_root) / EFFECTIVE_CONFIG_FILE
    print(f"export EXECUTION_ID={directory.name}")
    print(f"export EXECUTION_CONFIG_PATH={container_config}")
    print(f"# artefatos em {directory} (motor {metadata['decision_engine']})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
