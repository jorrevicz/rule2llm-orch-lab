"""Executor do protocolo experimental (M7-T06; metodologia Tabela 14, etapas 1–12).

    # uma execução
    .venv/bin/python -m scripts.experiment.run_experiment --scenario timeout --engine LLM
    # lote: cada cenário × repetição, Rules e LLM alternados (a ordem final é do M8)
    .venv/bin/python -m scripts.experiment.run_experiment --scenario normal --scenario timeout \\
        --engine RULES --engine LLM --repetitions 2

Etapas por execução:
 1. abre a execução (id, cenário, motor, repetição; config efetiva gravada);
 2–3. reset e subida limpa do ambiente apontado para a execução (`reset_environment`);
 4. readiness (`readiness`) — reprovação → execução inválida, sem carga;
 5. LLM: readiness do runtime, warm-up e inferência de prova (`llm_readiness`), `warmup.log`;
 6. metadados: versões, imagens, hardware, host, readiness, estado inicial;
 7–10. amostrador do Ollama + carga + falha + encerramento (`run_scenario.apply_scenario`);
 9. métricas do Prometheus exportadas (`queue_metrics.csv`, `container_stats.csv`);
 11. validação da integridade (`run_status`, `invalid_reason`);
 12. válida → consolidação das métricas (M7-T07); inválida → `invalid_runs.csv`.

No macOS, mantém `caffeinate` ativo durante todo o lote (o host não pode dormir: achado
do M6-T10). Execução inválida é registrada e repetida com a mesma configuração, até o
limite `--max-attempts`; nunca substituída em silêncio.
"""

import argparse
import csv
import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

from scripts.experiment import readiness as environment_readiness
from scripts.experiment.reset_environment import reset_environment
from scripts.experiment.versions import host_hardware, software_versions
from scripts.metrics.host_sampler import HostProcessSampler
from scripts.metrics.prometheus_export import export_container_stats, export_queue_metrics
from scripts.pilot.environment import DEFAULT_BASE_URL, REPO_ROOT, InspectionError
from scripts.pilot.llm_readiness import check as check_llm_readiness
from scripts.pilot.new_execution import METADATA_FILE, apply_llm_readiness, available_scenarios, open_execution
from scripts.scenarios.run_scenario import apply_scenario, finish
from shared.artifacts import Artifact
from shared.config import load_experiment_config
from shared.timestamps import utc_now_iso

DEFAULT_DATA_ROOT = REPO_ROOT / "data"
INVALID_RUNS_FILE = "invalid_runs.csv"
WARMUP_LOG_FILE = "warmup.log"
SUSPENSION_TOLERANCE_S = 2.0  # relógio de parede × monotônico: acima disso o host foi suspenso
REQUIRED_ARTIFACTS = (
    "execution_metadata.json",
    "experiment_config.yml",
    "initial_state.json",
    "reset.log",
    f"{Artifact.TASK_EVENTS}.jsonl",
    f"{Artifact.STATES}.jsonl",
    f"{Artifact.DECISIONS}.jsonl",
    f"{Artifact.MICROSERVICES_LOGS}.jsonl",
    f"{Artifact.FAULT_EVENTS}.jsonl",
    "workload.jsonl",
    "queue_metrics.csv",
    "container_stats.csv",
)


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def _metadata(directory: Path) -> dict:
    return json.loads((directory / METADATA_FILE).read_text(encoding="utf-8"))


def unreadable_artifacts(directory: Path) -> list[str]:
    """Artefatos obrigatórios presentes mas corrompidos (JSON/CSV que não abre)."""
    broken = []
    for name in REQUIRED_ARTIFACTS:
        path = directory / name
        if not path.is_file():
            continue
        try:
            if name.endswith(".jsonl"):
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        json.loads(line)
            elif name.endswith(".json"):
                json.loads(path.read_text(encoding="utf-8"))
            elif name.endswith(".csv"):
                with path.open(encoding="utf-8") as handle:
                    if not next(csv.reader(handle), None):
                        raise ValueError("no header")
        except (ValueError, UnicodeDecodeError):
            broken.append(name)
    return broken


def validate(directory: Path, summary: dict, *, http_errors: int) -> list[str]:
    """Etapa 11: falhas da bancada (não do mecanismo) que invalidam a execução."""
    reasons = []
    missing = [name for name in REQUIRED_ARTIFACTS if not (directory / name).is_file()]
    if missing:
        reasons.append(f"missing artifacts: {missing}")
    broken = unreadable_artifacts(directory)
    if broken:
        reasons.append(f"corrupted artifacts: {broken}")
    if not summary["settled"]:
        reasons.append("scenario did not settle (tasks not terminal or queues not drained)")
    if not summary["traceability_ok"]:
        reasons.append(f"traceability: {summary['traceability_errors'][:3]}")
    if http_errors:
        reasons.append(f"{http_errors} order requests failed at the API (bench failure, not a decision)")
    if abs(summary["wall_clock_s"] - summary["duration_s"]) > SUSPENSION_TOLERANCE_S:
        reasons.append(f"host suspended: wall clock {summary['wall_clock_s']} s vs monotonic {summary['duration_s']} s")
    return reasons


def record_invalid(data_root: Path, metadata: dict) -> None:
    path = data_root / ("pilot" if metadata["phase"] == "PILOT" else "experiment") / INVALID_RUNS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if new:
            writer.writerow(["execution_id", "scenario_id", "decision_engine", "repetition_id", "invalid_reason", "recorded_at"])
        writer.writerow([
            metadata["execution_id"], metadata["scenario_id"], metadata["decision_engine"],
            metadata["repetition_id"], metadata["invalid_reason"], utc_now_iso(),
        ])


def _mark_invalid(directory: Path, data_root: Path, reason: str) -> dict:
    metadata = _metadata(directory)
    metadata.update(run_status="INVALID", invalid_reason=reason, finished_at=utc_now_iso())
    _write_json(directory / METADATA_FILE, metadata)
    record_invalid(data_root, metadata)
    return metadata


def run_once(
    scenario_id: str,
    engine: str,
    repetition_id: int | None,
    *,
    data_root: Path = DEFAULT_DATA_ROOT,
    base_url: str = DEFAULT_BASE_URL,
    settle_timeout_s: float = 900.0,
    build: bool = True,
    scenarios_dir: Path | None = None,
) -> dict:
    # 1. execução aberta; a readiness do LLM vem depois do reset (etapa 5)
    directory = open_execution(
        data_root, scenario_id, engine=engine, llm_readiness=None, repetition_id=repetition_id,
        **({"scenarios_dir": scenarios_dir} if scenarios_dir else {}),
    )
    execution_id = directory.name
    config = load_experiment_config(directory / "experiment_config.yml")

    # 2–4. reset, subida limpa e readiness; falha da bancada aqui → execução inválida
    try:
        initial_state = reset_environment(execution_id, data_root, build=build)
        ready = environment_readiness.check(execution_id, data_root)
    except (InspectionError, OSError, ValueError) as error:
        return _mark_invalid(directory, data_root, f"bench_failure: {error.__class__.__name__}: {str(error)[:300]}")
    metadata = _metadata(directory)
    metadata.update(
        readiness_status=ready.status,
        readiness_timestamp=ready.timestamp,
        readiness_checks=[asdict(check) for check in ready.checks],
        initial_database_hashes={name: db["logical_sha256"] for name, db in initial_state["databases"].items()},
    )
    # 5. LLM: runtime, warm-up e inferência de prova
    if ready.status == "PASS" and engine == "LLM":
        llm = check_llm_readiness(config)
        apply_llm_readiness(metadata, llm)
        (directory / WARMUP_LOG_FILE).write_text(json.dumps(asdict(llm), indent=2) + "\n", encoding="utf-8")
    # 6. versões, imagens, hardware
    metadata["software"].update(software_versions())
    metadata["hardware"] = host_hardware()
    _write_json(directory / METADATA_FILE, metadata)
    if metadata["readiness_status"] != "PASS":
        failures = ready.failures or [metadata.get("invalid_reason") or "llm readiness failed"]
        return _mark_invalid(directory, data_root, "readiness_failed: " + "; ".join(map(str, failures)))

    # 7–10. carga, falha e encerramento, com o Ollama amostrado no host
    interval = config.metrics.sampling_interval_seconds
    sampler = HostProcessSampler(interval)
    sampler.start()
    try:
        scenario = apply_scenario(directory, execution_id, base_url, settle_timeout_s)
    finally:
        samples = sampler.stop()
    end_wall = time.time()
    # 9. métricas do Prometheus da janela da execução
    start = int(scenario.started_wall) - 1
    export_queue_metrics(directory, start, int(end_wall) + 1, interval)
    export_container_stats(directory, start, int(end_wall) + 1, interval, samples)
    summary = finish(directory, execution_id, scenario, data_root)

    # 11. integridade
    reasons = validate(directory, summary, http_errors=summary["http_errors"])
    metadata = _metadata(directory)
    metadata["load_started_at"] = scenario.load_started_at
    if reasons:
        _write_json(directory / METADATA_FILE, metadata)
        return _mark_invalid(directory, data_root, "; ".join(reasons))
    # 12. consolidação das métricas (M7-T07)
    metadata.update(run_status="VALID", invalid_reason=None, finished_at=utc_now_iso())
    _write_json(directory / METADATA_FILE, metadata)
    consolidate = _consolidator()
    if consolidate is not None:
        consolidate(directory)
    return metadata


def _consolidator():
    try:
        from scripts.analysis.consolidate import consolidate_execution
    except ImportError:  # antes do M7-T07
        return None
    return consolidate_execution


def schedule(scenarios: list[str], engines: list[str], repetitions: int) -> list[tuple[str, str, int]]:
    """Cenário × repetição, alternando a abordagem que abre cada repetição (metodologia §4.4.1)."""
    plan = []
    for scenario in scenarios:
        for repetition in range(1, repetitions + 1):
            order = engines if repetition % 2 else list(reversed(engines))
            plan += [(scenario, engine, repetition) for engine in order]
    return plan


def keep_host_awake() -> subprocess.Popen | None:
    if platform.system() != "Darwin":
        return None
    return subprocess.Popen(["caffeinate", "-dims", "-w", str(os.getpid())])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", action="append", required=True, choices=available_scenarios())
    parser.add_argument("--engine", action="append", required=True, choices=["RULES", "LLM"])
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--max-attempts", type=int, default=3, help="tentativas por execução inválida")
    parser.add_argument("--settle-timeout", type=float, default=900.0)
    args = parser.parse_args()

    awake = keep_host_awake()
    results = []
    try:
        for scenario, engine, repetition in schedule(args.scenario, args.engine, args.repetitions):
            for attempt in range(1, args.max_attempts + 1):
                metadata = run_once(scenario, engine, repetition, settle_timeout_s=args.settle_timeout)
                results.append({k: metadata.get(k) for k in ("execution_id", "scenario_id", "decision_engine", "repetition_id", "run_status", "invalid_reason")})
                print(json.dumps(results[-1], ensure_ascii=False), flush=True)
                if metadata["run_status"] == "VALID":
                    break
    finally:
        if awake is not None:
            awake.terminate()
    final = {(r["scenario_id"], r["decision_engine"], r["repetition_id"]): r["run_status"] for r in results}
    return 0 if all(status == "VALID" for status in final.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
