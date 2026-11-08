"""Seleção das execuções analisadas (CLAUDE §28; docs/15 §15.8)."""

import json
from pathlib import Path

PHASES = {"pilot": "PILOT", "experiment": "EXPERIMENT"}


def valid_executions(data_root: Path, phase: str, only: list[str] | None = None) -> list[Path]:
    """Execuções válidas da fase. Na amostra, exige `eligible_for_sample`."""
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
