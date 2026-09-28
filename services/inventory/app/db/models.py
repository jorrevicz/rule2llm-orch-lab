"""Esquema de `inventory.db` (piloto §23.2, docs/08 §8.3, D-02)."""

from enum import StrEnum


class ReservationStatus(StrEnum):
    RESERVED = "RESERVED"
    FAILED = "FAILED"


class ReservationRoute(StrEnum):
    PRIMARY = "primary"
    FALLBACK = "fallback"


class ProcessingResult(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


# processed_messages guarda, além do message_id (idempotência de transporte), o
# resultado e a resposta publicada: uma redelivery reemite a MESMA resposta (mesmo
# message_id), que o Orders deduplica. Colunas além do piloto §23.2: ver docs/09.
SCHEMA = """
CREATE TABLE IF NOT EXISTS reservations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL UNIQUE,
    order_id    TEXT NOT NULL,
    status      TEXT NOT NULL,
    route       TEXT NOT NULL,
    items_json  TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processed_messages (
    message_id     TEXT PRIMARY KEY,
    task_id        TEXT NOT NULL,
    event_type     TEXT NOT NULL,
    result         TEXT NOT NULL,
    response_json  TEXT NOT NULL,
    processed_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_processed_messages_task ON processed_messages (task_id);
"""
