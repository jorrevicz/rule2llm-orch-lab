"""Geração de identificadores com prefixo (docs/10 §10.1, docs/12 §12.2).

Dois formatos:
- sequencial (`ORD_000001`), para IDs derivados de uma sequência persistida;
- único (`MSG_<hex>`), para IDs gerados de forma independente pelos dois serviços.
A escolha de formato para cada identificador é feita no marco em que ele é emitido.
"""

import uuid
from enum import StrEnum


class IdPrefix(StrEnum):
    PILOT_EXECUTION = "PILOT"
    EXPERIMENT_EXECUTION = "EXP"
    ORDER = "ORD"
    TASK = "TASK"
    MESSAGE = "MSG"
    STATE = "STATE"
    DECISION = "DEC"


def sequential_id(prefix: IdPrefix, number: int, width: int = 6) -> str:
    """Formata um ID sequencial, por exemplo `ORD_000001`."""
    if number < 1:
        raise ValueError(f"number must be >= 1, got {number}")
    return f"{prefix}_{number:0{width}d}"


def unique_id(prefix: IdPrefix) -> str:
    """Gera um ID globalmente único, por exemplo `MSG_3f2a…`."""
    return f"{prefix}_{uuid.uuid4().hex}"
