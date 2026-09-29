"""Controle das falhas injetadas e registro em `fault_events` (D-20; metodologia §4.4).

Biblioteca comum aos scripts de falha (que ativam e desativam a falha) e ao
inventory-service (que a aplica). A falha ativa é um arquivo `fault_control.json` no
diretório da execução: presente = falha ativa; ausente = sem falha. Não há outro canal
entre a bancada e o serviço, e o decisor nunca lê esse arquivo (Tabela 9: o tipo de
falha injetada não é informação do decisor).

O sorteio é determinístico: `sampled(seed, task_id, attempt_number)`. Com o reset dos
bancos, os mesmos `task_id` se repetem, e as mesmas tentativas das mesmas tarefas
falham nas execuções Rules e LLM, qualquer que seja a ordem de consumo.
"""

import hashlib
import os
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.artifacts import Artifact, JsonlWriter, shard_path
from shared.timestamps import utc_now_iso

FAULT_CONTROL_FILE = "fault_control.json"
_HASH_SPACE = 2**64


class FaultControl(BaseModel):
    """Falha ativa na rota primária do Inventory."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fault_id: str
    type: Literal["intermittent_error", "timeout"]
    target: Literal["inventory.primary"]
    failure_probability: Annotated[float, Field(ge=0.0, le=1.0)]
    delay_ms: Annotated[int, Field(gt=0)] | None = None
    seed: int
    started_at: str

    @model_validator(mode="after")
    def _delay_only_for_timeout(self) -> "FaultControl":
        if (self.type == "timeout") != (self.delay_ms is not None):
            raise ValueError("delay_ms is required for timeout and only for timeout")
        return self


def sampled(seed: int, *keys: object, probability: float) -> bool:
    """Sorteio reprodutível: o mesmo (seed, chaves) dá sempre o mesmo resultado."""
    material = "|".join(str(part) for part in (seed, *keys)).encode()
    value = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
    return value / _HASH_SPACE < probability


def write_control(directory: Path, control: FaultControl) -> Path:
    """Ativa a falha. Escrita atômica: o serviço nunca lê um arquivo pela metade."""
    path = directory / FAULT_CONTROL_FILE
    temporary = path.with_suffix(".tmp")
    temporary.write_text(control.model_dump_json(), encoding="utf-8")
    os.replace(temporary, path)
    return path


def clear_control(directory: Path) -> None:
    (directory / FAULT_CONTROL_FILE).unlink(missing_ok=True)


def read_control(directory: Path) -> FaultControl | None:
    try:
        text = (directory / FAULT_CONTROL_FILE).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return FaultControl.model_validate_json(text)


class FaultEventRecorder:
    """`fault_events.<writer>.jsonl`: janelas de falha e cada efeito aplicado."""

    def __init__(self, writer: JsonlWriter, execution_id: str) -> None:
        self._writer = writer
        self._execution_id = execution_id

    @classmethod
    def for_process(cls, directory: Path, writer: str, execution_id: str) -> "FaultEventRecorder":
        return cls(JsonlWriter(shard_path(directory, Artifact.FAULT_EVENTS, writer)), execution_id)

    def record(self, event_type: str, **fields: object) -> dict:
        record = {"timestamp": utc_now_iso(), "execution_id": self._execution_id, "event_type": event_type, **fields}
        self._writer.write(record)
        return record
