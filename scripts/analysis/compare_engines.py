"""Comparação estatística Rules × LLM, pré-especificada (D-35; docs/15 §15.7; M9-T03).

    .venv/bin/python -m scripts.analysis.compare_engines --phase experiment

Lê somente `metrics_summary.csv` das execuções válidas, forma pares por cenário e repetição e
aplica o plano de `config/analysis_plan.yml`. Gera em `data/<fase>/analysis/`: `results.csv`
(uma linha por cenário × métrica, com os `execution_id` de origem), `analysis_summary.md` e
`analysis_metadata.json`. Não inventa valor: célula sem dado fica vazia e par incompleto é
descartado e relatado.
"""

import argparse
import csv
import hashlib
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict
from scipy import stats as scipy_stats

from scripts.analysis.executions import PHASES, valid_executions
from shared.timestamps import utc_now_iso

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = REPO_ROOT / "data"
DEFAULT_PLAN = REPO_ROOT / "config" / "analysis_plan.yml"
RECOVERY_METRIC = "recovery_restricted_ms"
ENGINES = ("RULES", "LLM")
RESULT_FIELDS = [
    "phase", "scenario_id", "metric", "family", "kind", "better", "n_pairs",
    *[f"{engine}_{stat}" for engine in ("rules", "llm") for stat in ("n", "mean", "median", "sd", "min", "max")],
    "mean_diff", "median_diff", "ci_low", "ci_high", "wilcoxon_statistic", "p_value", "p_holm", "rank_biserial",
    "relevance_threshold", "relevant", "label", "warning", "execution_pairs", "plan_sha256",
]


class _Plan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Bootstrap(_Plan):
    resamples: int
    confidence: float
    seed: int


class Relevance(_Plan):
    relative: float
    absolute_rate: float


class Survivorship(_Plan):
    completion_rate_floor: float
    completion_rate_gap: float


class MetricPlan(_Plan):
    name: str
    family: Literal["primary", "secondary"]
    kind: Literal["time", "rate", "count"]
    better: Literal["lower", "higher"]
    conditional_on_completion: bool = False


class AnalysisPlan(_Plan):
    alpha: float
    bootstrap: Bootstrap
    relevance: Relevance
    survivorship: Survivorship
    metrics: list[MetricPlan]


def load_plan(path: Path) -> tuple[AnalysisPlan, str]:
    raw = path.read_bytes()
    return AnalysisPlan.model_validate(yaml.safe_load(raw)), hashlib.sha256(raw).hexdigest()


@dataclass
class Run:
    execution_id: str
    scenario_id: str
    engine: str
    repetition_id: int
    git_commit: str | None
    values: dict[str, str]

    def number(self, name: str) -> float | None:
        value = self.values.get(name)
        return float(value) if value not in (None, "") else None

    @property
    def recovered(self) -> bool | None:
        return {"True": True, "False": False}.get(self.values.get("recovered", ""))


@dataclass
class Dataset:
    pairs: dict[str, dict[int, dict[str, Run]]] = field(default_factory=lambda: defaultdict(dict))
    warnings: list[str] = field(default_factory=list)


def load_runs(directories: list[Path]) -> list[Run]:
    runs = []
    for directory in directories:
        metadata = json.loads((directory / "execution_metadata.json").read_text(encoding="utf-8"))
        summary = directory / "metrics_summary.csv"
        if not summary.is_file() or metadata.get("repetition_id") is None:
            continue
        with summary.open(encoding="utf-8") as handle:
            values = next(csv.DictReader(handle))
        runs.append(
            Run(directory.name, metadata["scenario_id"], metadata["decision_engine"], int(metadata["repetition_id"]),
                metadata.get("git_commit"), values)
        )
    return runs


def build_pairs(runs: list[Run], *, strict: bool) -> Dataset:
    """Pares (cenário, repetição). Na amostra, execução duplicada é erro; no piloto, vale a última."""
    dataset = Dataset()
    latest: dict[tuple[str, int, str], Run] = {}
    for run in sorted(runs, key=lambda r: r.execution_id):
        key = (run.scenario_id, run.repetition_id, run.engine)
        if key in latest:
            if strict:
                raise SystemExit(f"duplicate valid execution for {key}: {latest[key].execution_id}, {run.execution_id}")
            dataset.warnings.append(f"{latest[key].execution_id} substituída por {run.execution_id} em {key}")
        latest[key] = run
    grouped: dict[tuple[str, int], dict[str, Run]] = defaultdict(dict)
    for (scenario, repetition, engine), run in latest.items():
        grouped[(scenario, repetition)][engine] = run
    for (scenario, repetition), pair in sorted(grouped.items()):
        if set(pair) == set(ENGINES):
            dataset.pairs[scenario][repetition] = pair
        else:
            dataset.warnings.append(f"par incompleto descartado: {scenario} repetição {repetition} ({sorted(pair)})")
    return dataset


# -- recuperação com censura (D-28) ----------------------------------------------------------


def recovery_restricted(pairs: dict[int, dict[str, Run]]) -> dict[str, float | None]:
    """Tempo após o fim da falha limitado a `τ`, o menor horizonte observado no cenário.

    Não recuperado vale `τ`: sabe-se só que o retorno, se houve, veio depois do horizonte.
    """
    runs = [run for pair in pairs.values() for run in pair.values()]
    horizons = [run.number("recovery_horizon_ms") for run in runs if run.recovered is not None]
    horizons = [h for h in horizons if h is not None]
    if not horizons:
        return {}
    tau = min(horizons)
    restricted: dict[str, float | None] = {}
    for run in runs:
        if run.recovered is None:
            restricted[run.execution_id] = None
        elif run.recovered:
            restricted[run.execution_id] = min(run.number("recovery_after_fault_end_ms"), tau)
        else:
            restricted[run.execution_id] = tau
    return restricted


def recovery_rates(pairs: dict[int, dict[str, Run]]) -> dict[str, tuple[int, int]]:
    rates = {}
    for engine in ENGINES:
        known = [pair[engine].recovered for pair in pairs.values() if pair[engine].recovered is not None]
        rates[engine] = (sum(known), len(known))
    return rates


# -- estatística -----------------------------------------------------------------------------


def describe(values: list[float]) -> dict:
    if not values:
        return {"n": 0, "mean": None, "median": None, "sd": None, "min": None, "max": None}
    return {
        "n": len(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "sd": statistics.stdev(values) if len(values) > 1 else None,
        "min": min(values),
        "max": max(values),
    }


def bootstrap_ci(differences: list[float], plan: Bootstrap) -> tuple[float, float]:
    """IC percentil da média das diferenças pareadas; a mesma seed em toda linha."""
    data = np.asarray(differences, dtype=float)
    rng = np.random.default_rng(plan.seed)
    indices = rng.integers(0, len(data), size=(plan.resamples, len(data)))
    means = data[indices].mean(axis=1)
    tail = (1 - plan.confidence) / 2 * 100
    return float(np.percentile(means, tail)), float(np.percentile(means, 100 - tail))


def wilcoxon(differences: list[float]) -> tuple[float | None, float | None, float | None]:
    """Estatística, p bicaudal e rank-biserial pareada (`> 0`: LLM maior). Zeros descartados."""
    nonzero = [d for d in differences if d != 0]
    if not nonzero:
        return None, None, None
    result = scipy_stats.wilcoxon(differences, zero_method="wilcox", alternative="two-sided")
    ranks = scipy_stats.rankdata([abs(d) for d in nonzero])
    positive = sum(rank for rank, d in zip(ranks, nonzero) if d > 0)
    negative = sum(rank for rank, d in zip(ranks, nonzero) if d < 0)
    return float(result.statistic), float(result.pvalue), float((positive - negative) / (positive + negative))


def holm(p_values: list[float | None]) -> list[float | None]:
    """Ajuste de Holm; entradas sem teste continuam sem valor."""
    tested = sorted((p, index) for index, p in enumerate(p_values) if p is not None)
    adjusted: list[float | None] = [None] * len(p_values)
    running = 0.0
    for order, (p, index) in enumerate(tested):
        running = max(running, min(1.0, (len(tested) - order) * p))
        adjusted[index] = running
    return adjusted


def relevance_threshold(metric: MetricPlan, rules_median: float | None, plan: AnalysisPlan) -> float | None:
    if metric.kind == "rate":
        return plan.relevance.absolute_rate
    return None if rules_median is None else abs(rules_median) * plan.relevance.relative


def label_of(row: dict, metric: MetricPlan, plan: AnalysisPlan) -> str:
    if row["n_pairs"] == 0:
        return "sem_pares"
    if row["p_value"] is None:
        return "sem_variacao"
    if metric.family == "secondary":
        return "descritiva"
    significant = row["p_holm"] is not None and row["p_holm"] < plan.alpha and (row["ci_low"] > 0 or row["ci_high"] < 0)
    if row["relevant"] and significant:
        llm_better = row["median_diff"] < 0 if metric.better == "lower" else row["median_diff"] > 0
        return "favorece_LLM" if llm_better else "favorece_RULES"
    return "inconclusivo" if row["relevant"] else "sem_evidencia"


def survivorship_warning(pairs: dict[int, dict[str, Run]], plan: AnalysisPlan) -> str | None:
    rates = {
        engine: [r for r in (pair[engine].number("completion_rate") for pair in pairs.values()) if r is not None]
        for engine in ENGINES
    }
    if not all(rates.values()):
        return None
    rules, llm = statistics.fmean(rates["RULES"]), statistics.fmean(rates["LLM"])
    limits = plan.survivorship
    if min(rules, llm) < limits.completion_rate_floor or abs(rules - llm) > limits.completion_rate_gap:
        return f"latência condicionada às concluídas: conclusão média Rules={rules:.3f}, LLM={llm:.3f}"
    return None


def _value(run: Run, metric: MetricPlan, restricted: dict[str, float | None]) -> float | None:
    return restricted.get(run.execution_id) if metric.name == RECOVERY_METRIC else run.number(metric.name)


def compare_metric(
    scenario: str, metric: MetricPlan, pairs: dict[int, dict[str, Run]], restricted: dict[str, float | None],
    plan: AnalysisPlan, plan_hash: str, phase: str,
) -> dict | None:
    values = {
        repetition: (_value(pair["RULES"], metric, restricted), _value(pair["LLM"], metric, restricted), pair)
        for repetition, pair in sorted(pairs.items())
    }
    rules_all = [rules for rules, _, _ in values.values() if rules is not None]
    llm_all = [llm for _, llm, _ in values.values() if llm is not None]
    if not rules_all and not llm_all:
        return None
    complete = [(rules, llm, pair) for rules, llm, pair in values.values() if rules is not None and llm is not None]
    differences = [llm - rules for rules, llm, _ in complete]

    row: dict = {
        "phase": PHASES[phase], "scenario_id": scenario, "metric": metric.name, "family": metric.family,
        "kind": metric.kind, "better": metric.better, "n_pairs": len(complete),
        **{f"rules_{key}": value for key, value in describe(rules_all).items()},
        **{f"llm_{key}": value for key, value in describe(llm_all).items()},
        "mean_diff": None, "median_diff": None, "ci_low": None, "ci_high": None, "wilcoxon_statistic": None,
        "p_value": None, "p_holm": None, "rank_biserial": None, "relevance_threshold": None, "relevant": None,
        "warning": survivorship_warning(pairs, plan) if metric.conditional_on_completion else None,
        "execution_pairs": ";".join(f"{pair['RULES'].execution_id}/{pair['LLM'].execution_id}" for _, _, pair in complete),
        "plan_sha256": plan_hash,
    }
    if differences:
        row["mean_diff"] = statistics.fmean(differences)
        row["median_diff"] = statistics.median(differences)
        row["ci_low"], row["ci_high"] = bootstrap_ci(differences, plan.bootstrap)
        row["wilcoxon_statistic"], row["p_value"], row["rank_biserial"] = wilcoxon(differences)
        threshold = relevance_threshold(metric, statistics.median([r for r, _, _ in complete]), plan)
        row["relevance_threshold"] = threshold
        row["relevant"] = None if threshold is None else abs(row["median_diff"]) >= threshold
    return row


def analyse(dataset: Dataset, plan: AnalysisPlan, plan_hash: str, phase: str) -> list[dict]:
    rows = []
    for scenario, pairs in sorted(dataset.pairs.items()):
        restricted = recovery_restricted(pairs)
        scenario_rows = [
            (metric, row)
            for metric in plan.metrics
            if (row := compare_metric(scenario, metric, pairs, restricted, plan, plan_hash, phase)) is not None
        ]
        primary = [row for metric, row in scenario_rows if metric.family == "primary"]
        for row, adjusted in zip(primary, holm([row["p_value"] for row in primary])):
            row["p_holm"] = adjusted
        for metric, row in scenario_rows:
            row["label"] = label_of(row, metric, plan)
            rows.append(row)
    return rows


# -- saída -----------------------------------------------------------------------------------


def _fmt(value: object) -> str:
    if value is None:
        return "—"
    return f"{value:.4g}" if isinstance(value, float) else str(value)


def summary_markdown(rows: list[dict], dataset: Dataset, plan: AnalysisPlan, plan_hash: str, phase: str) -> str:
    lines = ["# Análise comparativa Rules × LLM", ""]
    if phase == "pilot":
        lines += ["> **PILOTO — não é resultado da amostra.** Dados só para verificar o procedimento.", ""]
    lines += [
        f"Gerado em {utc_now_iso()}. Plano `config/analysis_plan.yml`, SHA-256 `{plan_hash}`.",
        f"Pares válidos por cenário e repetição; alfa = {plan.alpha}, Holm sobre as métricas primárias de cada cenário.",
        "`Δ = LLM − Rules`. `sem_evidencia` **não** significa equivalência.",
        "",
    ]
    for scenario, pairs in sorted(dataset.pairs.items()):
        rates = recovery_rates(pairs)
        lines += [f"## {scenario} ({len(pairs)} pares)", ""]
        lines += ["| Métrica primária | Rules (mediana) | LLM (mediana) | Δ mediana | IC 95% de Δ média | p Holm | Efeito | Conclusão |", "|---|---|---|---|---|---|---|---|"]
        for row in rows:
            if row["scenario_id"] == scenario and row["family"] == "primary":
                lines.append(
                    f"| `{row['metric']}` | {_fmt(row['rules_median'])} | {_fmt(row['llm_median'])} | {_fmt(row['median_diff'])} "
                    f"| [{_fmt(row['ci_low'])}; {_fmt(row['ci_high'])}] | {_fmt(row['p_holm'])} | {_fmt(row['rank_biserial'])} | {row['label']} |"
                )
        if rates["RULES"][1] or rates["LLM"][1]:
            lines += ["", f"Recuperadas: Rules {rates['RULES'][0]}/{rates['RULES'][1]}, LLM {rates['LLM'][0]}/{rates['LLM'][1]}."]
        warnings = sorted({row["warning"] for row in rows if row["scenario_id"] == scenario and row["warning"]})
        lines += ["", *[f"> Aviso: {warning}" for warning in warnings], ""] if warnings else [""]
    if dataset.warnings:
        lines += ["## Avisos da seleção", "", *[f"- {warning}" for warning in dataset.warnings], ""]
    lines += ["Métricas secundárias são descritivas e estão em `results.csv`.", ""]
    return "\n".join(lines)


def write_outputs(output: Path, rows: list[dict], markdown: str, metadata: dict) -> None:
    output.mkdir(parents=True, exist_ok=True)
    with (output / "results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    (output / "analysis_summary.md").write_text(markdown, encoding="utf-8")
    (output / "analysis_metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run(data_root: Path, phase: str, plan_path: Path, only: list[str] | None = None) -> Path:
    plan, plan_hash = load_plan(plan_path)
    runs = load_runs(valid_executions(data_root, phase, only))
    if not runs:
        raise SystemExit(f"no valid executions with metrics in {data_root / phase}")
    dataset = build_pairs(runs, strict=phase == "experiment")
    commits = sorted({run.git_commit or "unknown" for run in runs})
    if phase == "experiment" and len(commits) > 1:
        dataset.warnings.append(f"a amostra mistura commits: {commits}")
    rows = analyse(dataset, plan, plan_hash, phase)
    output = data_root / phase / "analysis"
    write_outputs(
        output, rows, summary_markdown(rows, dataset, plan, plan_hash, phase),
        {
            "phase": PHASES[phase],
            "eligible_for_sample": phase == "experiment",
            "created_at": utc_now_iso(),
            "plan_sha256": plan_hash,
            "git_commits": commits,
            "executions": [run.execution_id for run in runs],
            "warnings": dataset.warnings,
        },
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--phase", choices=sorted(PHASES), required=True)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--execution-id", action="append", default=None, help="restringe às execuções indicadas")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    args = parser.parse_args()
    print(run(args.data_root, args.phase, args.plan, args.execution_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
