"""Verifica a consistência dos artefatos coletados de uma execução (RNF-007, RNF-008).

    .venv/bin/python -m scripts.pilot.collect_artifacts --execution-id PILOT_0001
    .venv/bin/python -m scripts.pilot.check_traceability --execution-id PILOT_0001

Confere a cadeia `execution_id → task_id → state_id → decision_id → message_id/event_seq`:

- `execution_metadata.json`: existe e condiz com a execução (fase e elegibilidade);
- `task_events.jsonl`: por tarefa, a trajetória começa em `TASK_CREATED` e as
  ocorrências distintas têm `event_seq` 1, 2, 3, … sem lacunas; repetições da mesma
  mensagem apontam para o `event_seq` da primeira ocorrência;
- `states.jsonl`: cada snapshot é válido pelo contrato do `SYSTEM_STATE`, tem
  `state_id` único, pertence a uma tarefa da trajetória e sua janela `recent_events`
  existe na trajetória;
- `decisions.jsonl`: cada decisão é válida, tem `decision_id` único e aponta para um
  `state_id` registrado da mesma tarefa;
- `microservices_logs.jsonl`: todas as linhas são da mesma execução.

Base também para a validação de integridade do protocolo (M7, etapa 11).
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from scripts.pilot.environment import REPO_ROOT
from services.orders.app.observability.recorders import DecisionRecord
from shared.artifacts import Artifact, execution_dir
from shared.system_state import SystemState

DEFAULT_DATA_ROOT = REPO_ROOT / "data"
DEVELOPMENT_EXECUTION = "PILOT_0000"
EXPECTED_PHASE = {"PILOT": ("PILOT", False), "EXP": ("EXPERIMENT", True)}


@dataclass
class CheckResult:
    counts: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def check_metadata(directory: Path, execution_id: str, result: CheckResult) -> None:
    path = directory / "execution_metadata.json"
    if not path.exists():
        result.errors.append("execution_metadata.json missing")
        return
    metadata = json.loads(path.read_text(encoding="utf-8"))
    phase, eligible = EXPECTED_PHASE[execution_id.split("_", 1)[0]]
    if metadata.get("execution_id") != execution_id:
        result.errors.append(f"metadata execution_id is {metadata.get('execution_id')!r}")
    if metadata.get("phase") != phase or metadata.get("eligible_for_sample") is not eligible:
        result.errors.append(
            f"metadata phase/eligibility {metadata.get('phase')}/{metadata.get('eligible_for_sample')}"
            f" does not match {execution_id}"
        )


def check_trajectories(
    events: list[dict], execution_id: str, result: CheckResult
) -> dict[str, set[tuple[int, str]]]:
    """Retorna, por tarefa, o conjunto de (event_seq, event_type) registrados."""
    by_task: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        if event["execution_id"] != execution_id:
            result.errors.append(f"task_events: foreign execution_id {event['execution_id']}")
        by_task[event["task_id"]].append(event)

    trajectories: dict[str, set[tuple[int, str]]] = {}
    for task_id, task_events in by_task.items():
        first_seq_by_message: dict[str, int] = {}
        expected_seq = 1
        if task_events[0]["event_type"] != "TASK_CREATED":
            result.errors.append(f"{task_id}: trajectory does not start with TASK_CREATED")
        for event in task_events:
            message_id = event["message_id"]
            if message_id is not None and message_id in first_seq_by_message:
                if event["event_seq"] != first_seq_by_message[message_id]:
                    result.errors.append(f"{task_id}: repeated {message_id} with a new event_seq")
                continue
            if event["event_seq"] != expected_seq:
                result.errors.append(
                    f"{task_id}: event_seq {event['event_seq']} where {expected_seq} was expected"
                )
            expected_seq = event["event_seq"] + 1
            if message_id is not None:
                first_seq_by_message[message_id] = event["event_seq"]
        trajectories[task_id] = {(event["event_seq"], event["event_type"]) for event in task_events}
    return trajectories


def check_states(
    states: list[dict],
    execution_id: str,
    trajectories: dict[str, set[tuple[int, str]]],
    result: CheckResult,
) -> dict[str, str]:
    """Retorna `state_id → task_id` dos snapshots válidos."""
    state_tasks: dict[str, str] = {}
    for record in states:
        try:
            state = SystemState.model_validate(record["system_state"])
        except (KeyError, ValidationError) as error:
            result.errors.append(f"states: invalid SYSTEM_STATE ({error.__class__.__name__})")
            continue
        label = f"{state.task_id}/{state.state_id}"
        if state.state_id in state_tasks:
            result.errors.append(f"states: duplicated state_id {state.state_id}")
        state_tasks[state.state_id] = state.task_id
        if record.get("state_id") != state.state_id or record.get("task_id") != state.task_id:
            result.errors.append(f"states: {label} envelope fields differ from the snapshot")
        if state.execution_id != execution_id:
            result.errors.append(f"states: {label} has foreign execution_id")
        trajectory = trajectories.get(state.task_id)
        if trajectory is None:
            result.errors.append(f"states: {label} task not found in task_events")
            continue
        window = [(event.event_seq, event.event_type) for event in state.recent_events]
        if any(item not in trajectory for item in window):
            result.errors.append(f"states: {label} recent_events not found in the trajectory")
        if record.get("recent_events") != [event.model_dump(mode="json") for event in state.recent_events]:
            result.errors.append(f"states: {label} recent_events differ from the snapshot")
    return state_tasks


def check_decisions(
    decisions: list[dict], execution_id: str, state_tasks: dict[str, str], result: CheckResult
) -> None:
    seen: set[str] = set()
    for raw in decisions:
        try:
            decision = DecisionRecord.model_validate(raw)
        except ValidationError:
            result.errors.append("decisions: invalid record")
            continue
        if decision.decision_id in seen:
            result.errors.append(f"decisions: duplicated decision_id {decision.decision_id}")
        seen.add(decision.decision_id)
        if decision.execution_id != execution_id:
            result.errors.append(f"decisions: {decision.decision_id} has foreign execution_id")
        if state_tasks.get(decision.state_id) != decision.task_id:
            result.errors.append(
                f"decisions: {decision.decision_id} does not point to a state of {decision.task_id}"
            )


def check_logs(logs: list[dict], execution_id: str, result: CheckResult) -> None:
    foreign = sum(1 for line in logs if line.get("execution_id") != execution_id)
    if foreign:
        result.errors.append(f"microservices_logs: {foreign} lines from another execution")


def check(directory: Path, *, require_metadata: bool = True) -> CheckResult:
    execution_id = directory.name
    result = CheckResult()
    if require_metadata:
        check_metadata(directory, execution_id, result)

    events = _read_jsonl(directory / f"{Artifact.TASK_EVENTS}.jsonl")
    states = _read_jsonl(directory / f"{Artifact.STATES}.jsonl")
    decisions = _read_jsonl(directory / f"{Artifact.DECISIONS}.jsonl")
    logs = _read_jsonl(directory / f"{Artifact.MICROSERVICES_LOGS}.jsonl")
    result.counts = {
        "task_events": len(events),
        "states": len(states),
        "decisions": len(decisions),
        "microservices_logs": len(logs),
    }

    trajectories = check_trajectories(events, execution_id, result)
    state_tasks = check_states(states, execution_id, trajectories, result)
    check_decisions(decisions, execution_id, state_tasks, result)
    check_logs(logs, execution_id, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--execution-id", default=os.environ.get("EXECUTION_ID"))
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    args = parser.parse_args()
    if not args.execution_id:
        parser.error("--execution-id (ou EXECUTION_ID) é obrigatório")

    directory = execution_dir(args.data_root, args.execution_id)
    result = check(directory, require_metadata=args.execution_id != DEVELOPMENT_EXECUTION)
    print(json.dumps({"ok": result.ok, **result.__dict__}, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
