"""Gravação dos artefatos de decisão do orders-service (docs/10 §10.3).

Cada processo grava o seu próprio arquivo (`states.<writer>.jsonl`); a consolidação
em `states.jsonl` é feita por `scripts/pilot/collect_artifacts.py`.
"""

from pathlib import Path

from shared.artifacts import Artifact, JsonlWriter, shard_path
from shared.system_state import SystemState


class StateRecorder:
    """`states.jsonl`: o que exatamente o decisor sabia naquele instante (RF-031)."""

    def __init__(self, writer: JsonlWriter) -> None:
        self._writer = writer

    @classmethod
    def for_process(cls, artifacts_dir: Path, writer_name: str) -> "StateRecorder":
        return cls(JsonlWriter(shard_path(artifacts_dir, Artifact.STATES, writer_name)))

    def record(self, state: SystemState) -> None:
        snapshot = state.model_dump(mode="json")
        self._writer.write(
            {
                "execution_id": state.execution_id,
                "task_id": state.task_id,
                "state_id": state.state_id,
                "timestamp": state.timestamp,
                "current_event_seq": state.current_event_seq,
                "system_state": snapshot,
                "recent_events": snapshot["recent_events"],
            }
        )
