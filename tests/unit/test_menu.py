"""Menu dos procedimentos: plano dos ciclos, parada segura e conferência dos artefatos (M8-T07)."""

import sys

import pytest

import menu
from scripts.experiment.run_experiment import REQUIRED_ARTIFACTS

SCENARIOS = ["normal", "timeout"]


def test_each_cycle_is_one_repetition_and_the_opening_engine_alternates():
    assert menu.cycle_plan(SCENARIOS, ["RULES", "LLM"], 1) == [
        ("normal", "RULES", 1), ("normal", "LLM", 1), ("timeout", "RULES", 1), ("timeout", "LLM", 1),
    ]
    assert menu.cycle_plan(SCENARIOS, ["RULES", "LLM"], 2) == [
        ("normal", "LLM", 2), ("normal", "RULES", 2), ("timeout", "LLM", 2), ("timeout", "RULES", 2),
    ]


def test_execution_command_runs_one_repetition_with_the_cycle_number():
    assert menu.run_experiment_command("timeout", "LLM", 4) == [
        sys.executable, "-m", "scripts.experiment.run_experiment",
        "--scenario", "timeout", "--engine", "LLM", "--repetitions", "1", "--first-repetition", "4",
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", ["a", "b", "c"]),
        ("todos", ["a", "b", "c"]),
        ("3,1", ["c", "a"]),
        ("2 2 1", ["b", "a"]),
        ("4", None),
        ("0", None),
        ("x", None),
    ],
)
def test_selection_parsing(text, expected):
    assert menu.parse_selection(text, ["a", "b", "c"]) == expected


@pytest.mark.parametrize(("text", "expected"), [("", 1), ("3", 3), ("c", 0), ("C", 0), ("0", None), ("-1", None), ("x", None)])
def test_cycles_parsing(text, expected):
    assert menu.parse_cycles(text) == expected


def test_only_run_experiment_result_lines_are_kept():
    line = '{"execution_id": "PILOT_0070", "scenario_id": "normal", "decision_engine": "RULES", "repetition_id": 1, "run_status": "VALID", "invalid_reason": null}\n'
    assert menu.parse_result_line(line)["execution_id"] == "PILOT_0070"
    assert menu.parse_result_line("reset: docker compose down -v\n") is None
    assert menu.parse_result_line('{"event": "other"}\n') is None
    assert menu.parse_result_line("{not json\n") is None


def test_countdown_runs_the_whole_window_without_a_stop_request():
    sleeps = []
    assert menu.countdown(10, menu.StopRequest(), sleep=sleeps.append) is True
    assert sleeps == [1] * 10


def test_countdown_stops_as_soon_as_a_stop_is_requested():
    stop = menu.StopRequest()
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:
            stop.handle(2, None)

    assert menu.countdown(10, stop, sleep=sleep) is False
    assert len(sleeps) == 3


def _result(scenario, engine, repetition, status="VALID"):
    return {"execution_id": "PILOT_0001", "scenario_id": scenario, "decision_engine": engine,
            "repetition_id": repetition, "run_status": status, "invalid_reason": None}


def test_safe_stop_lets_the_running_execution_finish_and_skips_the_rest():
    stop = menu.StopRequest()
    plan = menu.cycle_plan(SCENARIOS, ["RULES", "LLM"], 1)
    commands = []

    def runner(command, stop_request):
        commands.append(command)
        if len(commands) == 2:
            stop_request.handle(2, None)  # Ctrl+C durante a 2ª execução
        scenario, engine, repetition = plan[len(commands) - 1]
        return [_result(scenario, engine, repetition)]

    report = menu.run_cycle(plan, stop, runner=runner)
    assert len(commands) == 2
    assert [r["decision_engine"] for r in report.results] == ["RULES", "LLM"]
    assert report.not_run == plan[2:]
    assert report.interrupted is None


def test_forced_stop_marks_the_execution_as_interrupted():
    stop = menu.StopRequest()
    plan = menu.cycle_plan(SCENARIOS, ["RULES", "LLM"], 1)

    def runner(command, stop_request):
        stop_request.forced = True
        stop_request.count = 2
        return []

    report = menu.run_cycle(plan, stop, runner=runner)
    assert report.interrupted == plan[0]
    assert report.not_run == plan[1:]


def test_cycle_without_stop_runs_every_execution():
    plan = menu.cycle_plan(SCENARIOS, ["RULES", "LLM"], 3)
    report = menu.run_cycle(plan, menu.StopRequest(), runner=lambda command, stop: [_result(*plan[0])])
    assert len(report.results) == len(plan)
    assert report.not_run == [] and report.interrupted is None


def test_missing_artifacts_include_the_consolidated_summary(tmp_path):
    for name in REQUIRED_ARTIFACTS:
        (tmp_path / name).write_text("x", encoding="utf-8")
    assert menu.missing_artifacts(tmp_path) == ["metrics_summary.csv"]
    (tmp_path / "metrics_summary.csv").write_text("a\n", encoding="utf-8")
    (tmp_path / "decisions.jsonl").unlink()
    assert menu.missing_artifacts(tmp_path) == ["decisions.jsonl"]


def test_every_menu_item_has_an_action():
    assert list(menu.ITEMS) == [str(n) for n in range(1, 12)]
    assert all(callable(item.action) for item in menu.ITEMS.values())
