"""Esquema de `inventory.db` (piloto §23.2, docs/08 §8.3, D-02)."""

from enum import StrEnum


class ReservationStatus(StrEnum):
    RESERVED = "RESERVED"
    FAILED = "FAILED"


class ReservationRoute(StrEnum):
    PRIMARY = "primary"
    FALLBACK = "fallback"


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
    message_id    TEXT PRIMARY KEY,
    task_id       TEXT NOT NULL,
    processed_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_processed_messages_task ON processed_messages (task_id);
"""
