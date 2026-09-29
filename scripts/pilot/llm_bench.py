"""Bancada de viabilidade do modelo LLM (M5-T08; critérios em docs/13-roadmap.md, M5).

    docker compose up -d --wait        # containers do experimento ativos (critério 1)
    .venv/bin/python -m scripts.pilot.llm_bench [--repetitions 5]

Roda o prompt fixo sobre um conjunto fixo de `SYSTEM_STATE`s representativos, cada um
repetido N vezes, depois da readiness/warm-up. Avalia SÓ os critérios técnicos,
fixados antes da medição (metodologia §4.3.7: troca de modelo apenas por
inviabilidade técnica):

1. modelo 100% na GPU com os containers ativos;
2. p95 de `llm_inference_ms` ≤ 7000 ms;
3. `prompt_eval_count` < `num_ctx` em todas as chamadas;
4. `done_reason = stop` em todas as chamadas.

Também relata, sem usar como critério: validade das decisões no Validator comum,
erros de leitura, ocorrência de target "null" como texto e estabilidade das respostas.
É dado de piloto: gravado em `data/pilot/llm_bench/`, nunca na amostra.
"""

import argparse
import json
import statistics
import sys
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path

from scripts.pilot.environment import REPO_ROOT, compose
from scripts.pilot.llm_readiness import DEFAULT_OLLAMA_URL, PROBE_LIMIT_MS, WARMUP_STATE
from scripts.pilot.llm_readiness import check as check_readiness
from services.orders.app.llm.decision_parser import parse_decision
from services.orders.app.llm.ollama_client import OllamaClient
from services.orders.app.llm.prompt_builder import PromptBuilder
from services.orders.app.orchestration.validator import DecisionValidator
from shared.config import ExperimentConfig, load_experiment_config
from shared.system_state import SystemState
from shared.timestamps import utc_now

P95_LIMIT_MS = PROBE_LIMIT_MS
BENCH_DIR = REPO_ROOT / "data" / "pilot" / "llm_bench"

DISPATCHED = {
    "task.phase": "DISPATCHED",
    "task.current_target": "inventory.primary",
    "task.current_service": "inventory-service",
    "current_event_seq": 3,
    "recent_events": [
        {"event_type": "TASK_CREATED", "attempt_number": 1, "event_seq": 1},
        {"event_type": "STOCK_RESERVATION_REQUESTED", "attempt_number": 1, "event_seq": 2},
        {"event_type": "INVENTORY_TIMEOUT", "attempt_number": 1, "event_seq": 3},
    ],
}
ON_FALLBACK = {
    **DISPATCHED,
    "task.phase": "FALLBACK_PROCESSING",
    "task.current_target": "inventory.fallback",
    "task.attempt_number": 3,
    "alternatives.fallback_used": True,
    "alternatives.alternative_targets": [],
}

# Estados representativos dos pontos de decisão do fluxo (docs/06 §6.7).
BENCH_STATES: dict[str, dict] = {
    "new_task": {},
    "new_task_unavailable": {"service.status": "unavailable"},
    "new_task_queue_pressure": {"messaging.queue_size": 25},
    "waiting_recovered": {"task.phase": "WAITING", "task.wait_count": 1},
    "timeout_attempt_1": {**DISPATCHED, "service.last_result": "timeout", "service.status": "degraded"},
    "timeout_attempt_2": {**DISPATCHED, "service.last_result": "timeout", "service.status": "degraded", "task.attempt_number": 2},
    "timeout_attempt_3": {**DISPATCHED, "service.last_result": "timeout", "service.status": "degraded", "task.attempt_number": 3},
    "timeout_attempt_3_no_fallback": {
        **DISPATCHED,
        "service.last_result": "timeout",
        "service.status": "degraded",
        "task.attempt_number": 3,
        "alternatives.fallback_available": False,
        "alternatives.alternative_targets": [],
    },
    "dispatched_unavailable": {**DISPATCHED, "service.last_result": "timeout", "service.status": "unavailable"},
    "transient_error": {**DISPATCHED, "service.last_result": "transient_error", "service.status": "degraded"},
    "invalid_data": {**DISPATCHED, "service.last_result": "invalid_data"},
    "fallback_failed": {**ON_FALLBACK, "service.last_result": "fallback_failed"},
}


def build_state(overrides: dict) -> SystemState:
    state = deepcopy(WARMUP_STATE)
    state.update({"execution_id": "PILOT_BENCH", "task_id": "TASK_BENCH", "state_id": "STATE_BENCH"})
    for dotted, value in overrides.items():
        node = state
        *parents, leaf = dotted.split(".")
        for key in parents:
            node = node[key]
        node[leaf] = value
    return SystemState.model_validate(state)


@dataclass
class Call:
    state: str
    wall_ms: float
    prompt_eval_count: int | None
    eval_count: int | None
    done_reason: str | None
    raw: str
    parse_error: str | None
    validation_error: str | None


@dataclass
class BenchReport:
    model: str
    repetitions: int
    readiness: dict
    placement_with_containers: str | None
    calls: list[Call] = field(default_factory=list)
    criteria: dict[str, dict] = field(default_factory=dict)
    observations: dict = field(default_factory=dict)

    @property
    def viable(self) -> bool:
        return all(criterion["pass"] for criterion in self.criteria.values())


def _p95(values: list[float]) -> float:
    return statistics.quantiles(values, n=20, method="inclusive")[18] if len(values) > 1 else values[0]


def evaluate(report: BenchReport, config: ExperimentConfig) -> None:
    latencies = [call.wall_ms for call in report.calls]
    prompt_counts = [call.prompt_eval_count or 0 for call in report.calls]
    reasons = Counter(call.done_reason for call in report.calls)
    report.criteria = {
        "1_model_fully_on_gpu": {
            "observed": report.placement_with_containers,
            "pass": report.placement_with_containers == "100% GPU",
        },
        "2_p95_inference_ms": {
            "observed": round(_p95(latencies), 1),
            "limit": P95_LIMIT_MS,
            "pass": _p95(latencies) <= P95_LIMIT_MS,
        },
        "3_prompt_fits_context": {
            "observed_max_prompt_tokens": max(prompt_counts),
            "num_ctx": config.llm.num_ctx,
            "pass": max(prompt_counts) < config.llm.num_ctx,
        },
        "4_no_truncated_response": {"observed": dict(reasons), "pass": set(reasons) == {"stop"}},
    }
    by_state: dict[str, set[str]] = {}
    for call in report.calls:
        by_state.setdefault(call.state, set()).add(call.raw)
    report.observations = {
        "latency_ms": {
            "p50": round(statistics.median(latencies), 1),
            "p95": round(_p95(latencies), 1),
            "max": round(max(latencies), 1),
        },
        "valid_decisions": sum(1 for c in report.calls if c.validation_error is None),
        "total_calls": len(report.calls),
        "parse_errors": dict(Counter(c.parse_error for c in report.calls if c.parse_error)),
        "validation_errors": dict(Counter(c.validation_error for c in report.calls if c.validation_error)),
        "string_null_target": sum(1 for c in report.calls if '"target": "null"' in c.raw),
        "states_with_varying_output": sorted(state for state, outputs in by_state.items() if len(outputs) > 1),
        "output_by_state": {state: sorted(outputs) for state, outputs in by_state.items()},
    }


def run(repetitions: int = 5, base_url: str = DEFAULT_OLLAMA_URL) -> BenchReport:
    config = load_experiment_config()
    readiness = check_readiness(config, base_url)
    running = compose("ps", "--status", "running", "--services").split()
    client = OllamaClient(base_url, config.llm)
    builder = PromptBuilder.from_file(REPO_ROOT / config.llm.prompt_template)
    validator = DecisionValidator(config)
    report = BenchReport(config.llm.model, repetitions, asdict(readiness), None)
    report.readiness["containers_running"] = running
    for _ in range(repetitions):
        for label, overrides in BENCH_STATES.items():
            state = build_state(overrides)
            generation = client.generate(builder.build(state))
            parsed = parse_decision(generation.text)
            validation = validator.validate(parsed.proposal, state)
            report.calls.append(
                Call(
                    state=label,
                    wall_ms=generation.wall_ms,
                    prompt_eval_count=generation.prompt_eval_count,
                    eval_count=generation.eval_count,
                    done_reason=generation.done_reason,
                    raw=generation.text,
                    parse_error=None if parsed.error is None else str(parsed.error),
                    validation_error=None if validation.valid else str(validation.error),
                )
            )
    loaded = next((m for m in client.running() if m.get("name") == config.llm.model), {})
    if loaded.get("size"):
        report.placement_with_containers = f"{round(100 * loaded['size_vram'] / loaded['size'])}% GPU"
    evaluate(report, config)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repetitions", type=int, default=5)
    args = parser.parse_args()

    report = run(args.repetitions)
    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    path = BENCH_DIR / f"LLM_BENCH_{utc_now().strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps({"viable": report.viable, **asdict(report)}, indent=2, ensure_ascii=False) + "\n")
    summary = {"viable": report.viable, "criteria": report.criteria, "observations": report.observations}
    summary["observations"] = {k: v for k, v in summary["observations"].items() if k != "output_by_state"}
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"# relatório completo: {path}", file=sys.stderr)
    return 0 if report.viable else 1


if __name__ == "__main__":
    sys.exit(main())
