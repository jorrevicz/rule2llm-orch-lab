"""Abre uma execução de piloto: aloca o `execution_id`, cria o diretório e grava
`execution_metadata.json` (docs/10 §10.3; metodologia Código 12).

    .venv/bin/python -m scripts.pilot.new_execution [--scenario normal]
    export EXECUTION_ID=PILOT_0001          # valor impresso pelo script
    docker compose up -d --build --wait

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
from pathlib import Path

from shared.artifacts import execution_dir
from shared.config import ExperimentConfig, load_experiment_config, resolve_config_path
from shared.ids import IdPrefix, sequential_id
from shared.timestamps import utc_now_iso

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = REPO_ROOT / "data"
METADATA_FILE = "execution_metadata.json"
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


def open_execution(
    data_root: Path = DEFAULT_DATA_ROOT, scenario_id: str = "normal", config_path: Path | None = None
) -> Path:
    resolved_config = resolve_config_path(config_path)
    config = load_experiment_config(resolved_config)
    if config.experiment.phase != "pilot":
        raise SystemExit("config phase is not 'pilot': this script only opens pilot executions")
    execution_id = next_pilot_id(data_root)
    directory = execution_dir(data_root, execution_id)
    directory.mkdir(parents=True)
    metadata = build_metadata(execution_id, scenario_id, config, resolved_config.resolve())
    (directory / METADATA_FILE).write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return directory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", default="normal")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    args = parser.parse_args()

    directory = open_execution(args.data_root, args.scenario)
    print(f"export EXECUTION_ID={directory.name}")
    print(f"# artefatos em {directory}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
