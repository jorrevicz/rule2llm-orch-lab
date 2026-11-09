"""Comparação estatística pré-especificada (D-35; docs/15 §15.7), com resultado conhecido."""

import csv
import json

import pytest

from scripts.analysis.compare_engines import (
    DEFAULT_PLAN,
    Run,
    analyse,
    bootstrap_ci,
    build_pairs,
    calibration_warnings,
    holm,
    load_plan,
    recovery_restricted,
    run,
    wilcoxon,
)

PLAN, PLAN_HASH = load_plan(DEFAULT_PLAN)


def make_run(number: int, engine: str, repetition: int, scenario: str = "timeout", **values) -> Run:
    return Run(f"EXP_{number:04d}", scenario, engine, repetition, "abc", {k: str(v) for k, v in values.items()})


def paired(n: int, rules, llm, scenario: str = "timeout") -> list[Run]:
    runs, number = [], 0
    for repetition in range(1, n + 1):
        for engine, make in (("RULES", rules), ("LLM", llm)):
            number += 1
            runs.append(make_run(number, engine, repetition, scenario, **make(repetition)))
    return runs


def rows_of(runs: list[Run]) -> dict:
    return {r["metric"]: r for r in analyse(build_pairs(runs, strict=True), PLAN, PLAN_HASH, "experiment")}


def test_calibration_warns_only_above_the_limit_and_outside_overload():
    utilization = {1: 0.70, 2: 0.71}
    make = lambda r: {"llm_fault_window_utilization": utilization[r]}
    runs = [
        *paired(2, lambda r: {}, make, "persistent_degradation"),
        *paired(2, lambda r: {}, lambda r: {"llm_fault_window_utilization": 0.99}, "overload"),
    ]

    warnings = calibration_warnings(build_pairs(runs, strict=True), PLAN)

    assert len(warnings) == 1
    assert "persistent_degradation, repetição 2" in warnings[0]
    assert "0.71" in warnings[0]


def test_wilcoxon_matches_the_exact_two_sided_value():
    statistic, p_value, effect = wilcoxon([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])

    assert statistic == 0.0
    assert p_value == pytest.approx(2 / 2**6)
    assert effect == 1.0


def test_wilcoxon_without_any_difference_has_no_test():
    assert wilcoxon([0.0, 0.0, 0.0]) == (None, None, None)


def test_holm_adjusts_sorted_p_values_and_keeps_missing_ones():
    adjusted = holm([0.01, None, 0.04, 0.03])
    assert adjusted[0] == pytest.approx(0.03)
    assert adjusted[1] is None
    assert adjusted[2] == pytest.approx(0.06)
    assert adjusted[3] == pytest.approx(0.06)


def test_bootstrap_is_reproducible_and_covers_the_mean():
    data = [1.0, 2.0, 3.0, 4.0, 10.0]

    assert bootstrap_ci(data, PLAN.bootstrap) == bootstrap_ci(data, PLAN.bootstrap)
    low, high = bootstrap_ci(data, PLAN.bootstrap)
    assert low < 4.0 < high


def test_clear_difference_is_labelled_for_the_better_engine():
    runs = paired(
        10,
        lambda r: {"latency_median_ms": 100 + r, "completion_rate": 1.0},
        lambda r: {"latency_median_ms": 300 + 3 * r, "completion_rate": 1.0},
    )

    row = rows_of(runs)["latency_median_ms"]

    assert row["n_pairs"] == 10
    assert row["median_diff"] > 0
    assert row["p_holm"] < 0.05
    assert row["label"] == "favorece_RULES"
    assert row["warning"] is None


def test_llm_better_on_a_higher_is_better_metric_is_only_descriptive_when_secondary():
    runs = paired(10, lambda r: {"completion_rate": 0.7}, lambda r: {"completion_rate": 0.9 + r / 1000})

    assert rows_of(runs)["completion_rate"]["label"] == "descritiva"


def test_identical_results_have_no_test_and_no_conclusion():
    row = rows_of(paired(8, lambda r: {"undue_failure_rate": 0.1}, lambda r: {"undue_failure_rate": 0.1}))["undue_failure_rate"]

    assert row["p_value"] is None and row["label"] == "sem_variacao"


def test_relevant_difference_without_significance_is_inconclusive():
    # 3 pares nunca alcançam p < 0,05; a diferença é relevante, mas sem evidência suficiente.
    row = rows_of(paired(3, lambda r: {"latency_median_ms": 100 + r, "completion_rate": 1.0}, lambda r: {"latency_median_ms": 200 + r, "completion_rate": 1.0}))["latency_median_ms"]

    assert row["label"] == "inconclusivo"


def test_small_difference_is_not_a_finding_even_if_consistent():
    row = rows_of(paired(10, lambda r: {"latency_median_ms": 1000 + r, "completion_rate": 1.0}, lambda r: {"latency_median_ms": 1001 + r * 1.001, "completion_rate": 1.0}))["latency_median_ms"]

    assert row["relevant"] is False and row["label"] == "sem_evidencia"


def test_latency_of_survivors_carries_a_warning():
    row = rows_of(paired(8, lambda r: {"latency_median_ms": 100 + r, "completion_rate": 1.0}, lambda r: {"latency_median_ms": 90 + r, "completion_rate": 0.1}))["latency_median_ms"]

    assert "condicionada" in row["warning"]


def test_llm_only_metrics_have_no_pairs():
    row = rows_of(paired(4, lambda r: {"latency_median_ms": 1}, lambda r: {"latency_median_ms": 1, "llm_compute_total_ms": 5 + r}))["llm_compute_total_ms"]

    assert row["n_pairs"] == 0 and row["label"] == "sem_pares" and row["llm_n"] == 4


def test_non_recovered_run_is_censored_at_the_common_horizon():
    runs = [
        make_run(1, "RULES", 1, recovered=True, recovery_after_fault_end_ms=500, recovery_horizon_ms=9000),
        make_run(2, "LLM", 1, recovered=False, recovery_horizon_ms=8000),
        make_run(3, "RULES", 2, recovered=True, recovery_after_fault_end_ms=8500, recovery_horizon_ms=9000),
        make_run(4, "LLM", 2, recovered="", recovery_horizon_ms=8000),
    ]

    restricted = recovery_restricted(build_pairs(runs, strict=True).pairs["timeout"])

    assert restricted["EXP_0001"] == 500.0
    assert restricted["EXP_0002"] == 8000.0      # não recuperado = τ, o menor horizonte
    assert restricted["EXP_0003"] == 8000.0      # limitado a τ
    assert restricted["EXP_0004"] is None        # sem referência: não calculável


def test_incomplete_pairs_are_dropped_and_reported():
    dataset = build_pairs([make_run(1, "RULES", 1, latency_median_ms=1)], strict=True)

    assert not dataset.pairs and "incompleto" in dataset.warnings[0]


def test_duplicate_execution_is_an_error_in_the_sample_and_last_wins_in_the_pilot():
    runs = [make_run(1, "RULES", 1, a=1), make_run(2, "RULES", 1, a=2), make_run(3, "LLM", 1, a=3)]

    with pytest.raises(SystemExit, match="duplicate"):
        build_pairs(runs, strict=True)
    dataset = build_pairs(runs, strict=False)
    assert dataset.pairs["timeout"][1]["RULES"].execution_id == "EXP_0002"
    assert dataset.warnings


def _write_run(root, name, engine, repetition, phase, **values):
    directory = root / phase.lower() / name
    directory.mkdir(parents=True)
    (directory / "execution_metadata.json").write_text(json.dumps({
        "execution_id": name, "scenario_id": "timeout", "decision_engine": engine, "repetition_id": repetition,
        "run_status": "VALID", "phase": phase, "eligible_for_sample": phase == "EXPERIMENT", "git_commit": "abc",
    }))
    with (directory / "metrics_summary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(values))
        writer.writeheader()
        writer.writerow(values)


def test_outputs_trace_each_row_to_its_executions_and_flag_the_pilot(tmp_path):
    (tmp_path / "pilot").mkdir()
    for repetition in range(1, 4):
        _write_run(tmp_path, f"PILOT_{repetition:04d}", "RULES", repetition, "PILOT", latency_median_ms=100 + repetition, completion_rate=1.0)
        _write_run(tmp_path, f"PILOT_{repetition + 10:04d}", "LLM", repetition, "PILOT", latency_median_ms=300, completion_rate=1.0)

    output = run(tmp_path, "pilot", DEFAULT_PLAN)

    with (output / "results.csv").open() as handle:
        rows = {row["metric"]: row for row in csv.DictReader(handle)}
    assert rows["latency_median_ms"]["execution_pairs"].startswith("PILOT_0001/PILOT_0011")
    assert rows["latency_median_ms"]["plan_sha256"] == PLAN_HASH
    assert "PILOTO — não é resultado da amostra" in (output / "analysis_summary.md").read_text()
    assert json.loads((output / "analysis_metadata.json").read_text())["eligible_for_sample"] is False
