"""Reavaliação offline dos mesmos estados pelos dois motores (D-34; docs/15 §15.6).

    .venv/bin/python -m scripts.analysis.replay_decisions --phase pilot --per-stratum 20

Toma os `SYSTEM_STATE` gravados nas execuções válidas, sorteia uma amostra estratificada e
determinística e submete cada estado, em sequência, ao Rules e ao LLM, com o mesmo Validator.
Sem filas, timers ou concorrência: isola o mecanismo decisório. Não executa nenhuma ação e não
integra a amostra de execuções; o desempenho sistêmico continua vindo das execuções.

Saída em `data/<fase>/replay/REPLAY_<instante>/`: `replay_decisions.jsonl`,
`replay_summary.csv` e `replay_metadata.json`.
"""

import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from scripts.analysis.decision_adequacy import ADEQUATE, INADEQUATE, NO_DECISION, adequate_actions, classify_opportunity
from scripts.pilot.environment import REPO_ROOT
from scripts.pilot.llm_readiness import DEFAULT_OLLAMA_URL
from scripts.pilot.llm_readiness import check as check_llm_readiness
from scripts.pilot.new_execution import git_commit
from services.orders.app.llm.llm_engine import LLMDecisionEngine
from services.orders.app.llm.ollama_client import OllamaClient
from services.orders.app.llm.prompt_builder import PromptBuilder
from services.orders.app.orchestration.decision_engine import LLM_DECISION_TIMEOUT
from services.orders.app.orchestration.rules_engine import RulesDecisionEngine
from services.orders.app.orchestration.validator import DecisionValidator
from shared.artifacts import Artifact
from shared.config import ExperimentConfig, load_experiment_config
from shared.system_state import SystemState
from shared.timestamps import utc_now, utc_now_iso

DEFAULT_DATA_ROOT = REPO_ROOT / "data"
PHASES = {"pilot": "PILOT", "experiment": "EXPERIMENT"}
SUMMARY_FIELDS = [
    "stratum", "states", "agreement_rate", "rules_valid_rate", "llm_valid_rate", "rules_opportunity_rate",
    "llm_opportunity_rate", "llm_without_proposal", "llm_actions",
]


@dataclass(frozen=True)
class SourceState:
    execution_id: str
    state_id: str
    state: dict
    config: ExperimentConfig


def stratum(state: dict, config: ExperimentConfig) -> str:
    """Condição operacional visível ao decisor; estados iguais nestes campos são equivalentes."""
    task, service = state["task"], state["service"]
    pressure = state["messaging"]["queue_size"] >= config.messaging.queue_high_watermark
    rule, _ = adequate_actions(state, config)
    return "|".join(
        str(part)
        for part in (
            rule, task["phase"], service["status"], service.get("last_result"), f"attempt={task['attempt_number']}",
            f"waits={task['wait_count']}", f"fallback_used={state['alternatives']['fallback_used']}", f"pressure={pressure}",
        )
    )


def valid_executions(data_root: Path, phase: str, only: list[str] | None = None) -> list[Path]:
    """Execuções válidas da fase. Na amostra, exige `eligible_for_sample` (CLAUDE §28)."""
    selected = []
    for directory in sorted((data_root / phase).iterdir()):
        path = directory / "execution_metadata.json"
        if not path.is_file() or (only and directory.name not in only):
            continue
        metadata = json.loads(path.read_text(encoding="utf-8"))
        if metadata.get("run_status") != "VALID" or metadata.get("phase") != PHASES[phase]:
            continue
        if phase == "experiment" and metadata.get("eligible_for_sample") is not True:
            continue
        selected.append(directory)
    return selected


def load_states(directories: list[Path]) -> list[SourceState]:
    states = []
    for directory in directories:
        config = load_experiment_config(directory / "experiment_config.yml")
        for line in (directory / f"{Artifact.STATES}.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                states.append(SourceState(directory.name, record["state_id"], record["system_state"], config))
    return states


def select_states(states: list[SourceState], per_stratum: int, seed: int) -> dict[str, list[SourceState]]:
    """Amostra por estrato; a ordem é um hash de (seed, state_id), sem depender da leitura."""
    strata: dict[str, list[SourceState]] = defaultdict(list)
    for source in states:
        strata[stratum(source.state, source.config)].append(source)
    key = lambda source: hashlib.sha256(f"{seed}|{source.state_id}".encode()).hexdigest()  # noqa: E731
    return {name: sorted(group, key=key)[:per_stratum] for name, group in sorted(strata.items())}


def _judge(output, state_model: SystemState, source: SourceState) -> dict:
    timed_out = output.failure == LLM_DECISION_TIMEOUT
    validation = DecisionValidator(source.config).validate(output.proposal, state_model, timed_out=timed_out)
    action = output.proposal.action if output.proposal else None
    rule, actions, opportunity = classify_opportunity(source.state, action, source.config)
    return {
        "action": action,
        "target": output.proposal.target if output.proposal else None,
        "reason_code": output.proposal.reason_code if output.proposal else None,
        "failure": output.failure,
        "valid": validation.valid,
        "validation_error": None if validation.error is None else str(validation.error),
        "opportunity_rule": rule,
        "adequate_actions": sorted(actions) if actions is not None else None,
        "opportunity": opportunity,
    }


def replay_one(source: SourceState, name: str, llm_engine) -> dict:
    state_model = SystemState.model_validate(source.state)
    rules = RulesDecisionEngine(source.config).decide(state_model)
    llm = llm_engine.decide(state_model)
    return {
        "source_execution_id": source.execution_id,
        "state_id": source.state_id,
        "stratum": name,
        "rules": _judge(rules, state_model, source),
        "llm": {
            **_judge(llm, state_model, source),
            "raw_response": llm.raw_response,
            "llm_inference_ms": llm.llm_inference_ms,
            "llm_compute_ms": llm.llm_compute_ms,
        },
    }


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _opportunity_rate(rows: list[dict], side: str) -> float | None:
    classified = [row for row in rows if row[side]["opportunity"] in {ADEQUATE, INADEQUATE}]
    return _rate(sum(1 for row in classified if row[side]["opportunity"] == ADEQUATE), len(classified))


def summarise(rows: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[row["stratum"]].append(row)
        groups["TOTAL"].append(row)
    summary = []
    for name, group in sorted(groups.items()):
        actions: dict[str, int] = defaultdict(int)
        for row in group:
            actions[str(row["llm"]["action"])] += 1
        summary.append(
            {
                "stratum": name,
                "states": len(group),
                "agreement_rate": _rate(sum(1 for r in group if r["rules"]["action"] == r["llm"]["action"]), len(group)),
                "rules_valid_rate": _rate(sum(1 for r in group if r["rules"]["valid"]), len(group)),
                "llm_valid_rate": _rate(sum(1 for r in group if r["llm"]["valid"]), len(group)),
                "rules_opportunity_rate": _opportunity_rate(group, "rules"),
                "llm_opportunity_rate": _opportunity_rate(group, "llm"),
                "llm_without_proposal": sum(1 for r in group if r["llm"]["opportunity"] == NO_DECISION),
                "llm_actions": json.dumps(dict(sorted(actions.items()))),
            }
        )
    return summary


def write_outputs(output: Path, rows: list[dict], summary: list[dict], metadata: dict) -> None:
    output.mkdir(parents=True)
    with (output / "replay_decisions.jsonl").open("w", encoding="utf-8") as handle:
        handle.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    with (output / "replay_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(summary)
    (output / "replay_metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> Path:
    directories = valid_executions(args.data_root, args.phase, args.execution_id)
    if not directories:
        raise SystemExit(f"no valid executions in {args.data_root / args.phase}")
    base = load_experiment_config()
    readiness = check_llm_readiness(base, args.ollama_url)
    if readiness.status != "PASS":
        raise SystemExit(f"LLM readiness failed: {readiness.failures}")
    engine = LLMDecisionEngine(OllamaClient(args.ollama_url, base.llm), PromptBuilder.from_file(REPO_ROOT / base.llm.prompt_template))

    selected = select_states(load_states(directories), args.per_stratum, args.seed)
    rows = []
    for name, group in selected.items():
        for source in group:
            rows.append(replay_one(source, name, engine))
    output = args.data_root / args.phase / "replay" / f"REPLAY_{utc_now().strftime('%Y%m%dT%H%M%SZ')}"
    write_outputs(
        output,
        rows,
        summarise(rows),
        {
            "phase": PHASES[args.phase],
            "eligible_for_sample": False,
            "kind": "offline_replay",
            "created_at": utc_now_iso(),
            "git_commit": git_commit(),
            "seed": args.seed,
            "per_stratum": args.per_stratum,
            "states_replayed": len(rows),
            "strata": len(selected),
            "source_executions": [directory.name for directory in directories],
            "llm": base.llm.model_dump(),
            "prompt_template_hash": PromptBuilder.from_file(REPO_ROOT / base.llm.prompt_template).template_hash,
            "llm_runtime_version": readiness.runtime_version,
            "model_digest": readiness.model_digest,
        },
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--phase", choices=sorted(PHASES), required=True)
    parser.add_argument("--per-stratum", type=int, default=20, help="estados por estrato")
    parser.add_argument("--seed", type=int, default=7, help="seed da seleção determinística")
    parser.add_argument("--execution-id", action="append", default=None, help="restringe às execuções indicadas")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    print(run(parser.parse_args()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
