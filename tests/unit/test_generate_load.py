"""Plano de carga determinístico em malha aberta (M6-T07; RF-040, D-03)."""

import pytest

from scripts.workload.generate_load import UNKNOWN_SKU, build_plan, send_offsets
from shared.catalog import load_dataset
from shared.config import FaultSection, load_experiment_config, load_scenario
from tests.unit.test_config import REPO_CONFIG

SCENARIOS = REPO_CONFIG.parent / "scenarios"
DATASET = load_dataset(REPO_CONFIG.parents[1] / "datasets" / "orders_v1.json")


def scenario_config(name: str, **workload):
    scenario = load_scenario(SCENARIOS / f"{name}.yml")
    base = load_experiment_config(REPO_CONFIG)
    return base.model_copy(
        update={"workload": scenario.workload.model_copy(update=workload), "fault": scenario.fault}
    )


def test_normal_plan_sends_the_first_orders_of_the_dataset_at_a_fixed_rate():
    config = scenario_config("normal")

    plan = build_plan(config)

    assert [p.index for p in plan] == list(range(1, config.workload.requests + 1))
    assert [p.offset_s for p in plan] == [i / config.workload.rate_per_second for i in range(len(plan))]
    assert all(p.items == DATASET.orders[p.index - 1].items and p.replaced_sku is None for p in plan)


def test_plan_is_identical_for_every_run_of_the_same_scenario():
    for name in ("normal", "overload", "inconsistent_data"):
        assert build_plan(scenario_config(name)) == build_plan(scenario_config(name))


def test_overload_window_raises_the_send_rate_only_inside_the_window():
    config = scenario_config("overload")
    fault = config.fault
    offsets = send_offsets(config)
    start, end = fault.start_after_seconds, fault.start_after_seconds + fault.duration_seconds

    gaps = [(a, b - a) for a, b in zip(offsets, offsets[1:])]
    for at, gap in gaps:
        rate = fault.overload_rate_per_second if start <= at < end else config.workload.rate_per_second
        assert gap == pytest.approx(1 / rate, abs=1e-5)
    inside = sum(start <= at < end for at in offsets)
    assert inside == pytest.approx(fault.duration_seconds * fault.overload_rate_per_second, abs=1)


def test_inconsistent_orders_are_drawn_only_inside_the_window():
    config = scenario_config("inconsistent_data", requests=40)
    fault = config.fault
    plan = build_plan(config)

    replaced = [p for p in plan if p.replaced_sku is not None]
    assert replaced, "a janela deve conter pedidos inconsistentes"
    for planned in plan:
        inside = fault.start_after_seconds <= planned.offset_s < fault.start_after_seconds + fault.duration_seconds
        original = DATASET.orders[planned.index - 1].items
        if planned.replaced_sku is None:
            assert planned.items == original
        else:
            assert inside
            assert planned.items[0]["sku"] == UNKNOWN_SKU and planned.replaced_sku == original[0]["sku"]
            assert planned.items[1:] == original[1:]


def test_other_scenarios_never_alter_orders():
    for name in ("normal", "overload", "intermittent_failure", "timeout", "recovery"):
        assert all(p.replaced_sku is None for p in build_plan(scenario_config(name)))


def test_workload_seed_must_be_the_dataset_seed():
    with pytest.raises(ValueError, match="dataset seed"):
        build_plan(scenario_config("normal", seed=7))
