"""Artefatos de rastreabilidade de uma execução (docs/10 §10.2).

Diretório: `<data_root>/pilot/<EXECUTION_ID>/` para `PILOT_*` e
`<data_root>/experiment/<EXECUTION_ID>/` para `EXP_*`. O destino é derivado do
próprio `execution_id`, de modo que um piloto nunca grava na pasta da amostra.

Cada processo grava o seu próprio arquivo (`<artefato>.<writer>.jsonl`, ex.:
`states.orders-api.jsonl`): containers diferentes não escrevem no mesmo arquivo.
`scripts/pilot/collect_artifacts.py` consolida os arquivos no fim da execução
(`states.jsonl`, `decisions.jsonl`, ...).
"""

import json
import threading
from enum import StrEnum
from pathlib import Path
from typing import Any

PHASE_DIRS = {"PILOT": "pilot", "EXP": "experiment"}


class Artifact(StrEnum):
    STATES = "states"
    DECISIONS = "decisions"
    TASK_EVENTS = "task_events"
    MICROSERVICES_LOGS = "microservices_logs"
    FAULT_EVENTS = "fault_events"


def execution_dir(data_root: Path, execution_id: str) -> Path:
    prefix = execution_id.split("_", 1)[0]
    if prefix not in PHASE_DIRS or "_" not in execution_id:
        raise ValueError(f"execution_id must start with PILOT_ or EXP_: {execution_id!r}")
    return data_root / PHASE_DIRS[prefix] / execution_id


def shard_path(directory: Path, artifact: Artifact, writer: str) -> Path:
    return directory / f"{artifact}.{writer}.jsonl"


class JsonlWriter:
    """Acrescenta um registro JSON por linha; seguro entre threads do mesmo processo."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def write(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
