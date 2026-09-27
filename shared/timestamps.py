"""Timestamps normalizados: UTC, ISO 8601, precisão de milissegundos (CLAUDE §37).

Formato único dos artefatos experimentais: `2026-08-27T12:00:00.000Z`.
"""

from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_iso(moment: datetime) -> str:
    """Serializa um datetime *aware* como `YYYY-MM-DDTHH:MM:SS.mmmZ` em UTC."""
    if moment.tzinfo is None:
        raise ValueError("naive datetime is not allowed; use an aware UTC datetime")
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def utc_now_iso() -> str:
    return to_iso(utc_now())


def parse_iso(value: str) -> datetime:
    """Lê um timestamp ISO 8601 com offset explícito (`Z` ou `+HH:MM`) e devolve em UTC."""
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError(f"timestamp without timezone: {value!r}")
    return moment.astimezone(UTC)


def elapsed_ms(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() * 1000.0
