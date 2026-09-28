"""Logs estruturados dos microsserviços → `microservices_logs.<writer>.jsonl` (RF-034).

Cada registro é uma linha JSON com `timestamp` (UTC, ISO 8601, ms), `level`,
`service`, `logger`, `message`, o `execution_id` do processo e os campos de
correlação (CLAUDE §36), passados num dicionário próprio:

    logger.info("reservation request handled", extra=correlated(task_id=..., message_id=...))

O dicionário separado evita colisão com atributos que bibliotecas injetam no
registro de log (o Celery, por exemplo, grava o UUID da tarefa Celery em `task_id`).
"""

import logging
from datetime import UTC, datetime
from pathlib import Path

from shared.artifacts import Artifact, JsonlWriter, shard_path
from shared.timestamps import to_iso

CORRELATION_KEY = "correlation"

CORRELATION_FIELDS = (
    "task_id",
    "order_id",
    "message_id",
    "event_seq",
    "event_type",
    "attempt_number",
    "state_id",
    "decision_id",
    "redelivered",
    "outcome",
)


class JsonlLogHandler(logging.Handler):
    def __init__(self, writer: JsonlWriter, *, service: str, execution_id: str) -> None:
        super().__init__(level=logging.INFO)
        self._writer = writer
        self._service = service
        self._execution_id = execution_id

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._writer.write(self.to_record(record))
        except Exception:  # noqa: BLE001 — falha de log não pode derrubar o serviço
            self.handleError(record)

    def to_record(self, record: logging.LogRecord) -> dict:
        entry = {
            "timestamp": to_iso(datetime.fromtimestamp(record.created, UTC)),
            "level": record.levelname,
            "service": self._service,
            "logger": record.name,
            "execution_id": self._execution_id,
            "message": record.getMessage(),
        }
        fields = getattr(record, CORRELATION_KEY, None) or {}
        for field in CORRELATION_FIELDS:
            value = fields.get(field)
            if value is not None:
                entry[field] = str(value) if hasattr(value, "value") else value
        if record.exc_info:
            entry["exception"] = logging.Formatter().formatException(record.exc_info)
        return entry


def correlated(**fields: object) -> dict[str, dict[str, object]]:
    """`extra` de logging com os campos de correlação do registro."""
    return {CORRELATION_KEY: fields}


def jsonl_log_handler(artifacts_dir: Path, *, service: str, execution_id: str) -> JsonlLogHandler:
    writer = JsonlWriter(shard_path(artifacts_dir, Artifact.MICROSERVICES_LOGS, service))
    return JsonlLogHandler(writer, service=service, execution_id=execution_id)


def install(logger: logging.Logger, handler: JsonlLogHandler) -> None:
    """Acrescenta o handler uma única vez ao logger informado."""
    if not any(isinstance(existing, JsonlLogHandler) for existing in logger.handlers):
        logger.addHandler(handler)
    if logger.level == logging.NOTSET or logger.level > logging.INFO:
        logger.setLevel(logging.INFO)


def uninstall(logger: logging.Logger) -> None:
    for handler in [h for h in logger.handlers if isinstance(h, JsonlLogHandler)]:
        logger.removeHandler(handler)
