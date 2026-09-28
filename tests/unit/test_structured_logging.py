import json
import logging
import re

import pytest

from shared.artifacts import Artifact
from shared.structured_logging import correlated, install, jsonl_log_handler, uninstall
from shared.task import TaskResult

ISO_UTC_MS = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z"


@pytest.fixture
def logger(tmp_path):
    handler = jsonl_log_handler(tmp_path, service="inventory-worker", execution_id="PILOT_TEST")
    log = logging.getLogger("test.structured")
    log.propagate = False
    install(log, handler)
    yield log
    uninstall(log)


def _records(tmp_path) -> list[dict]:
    path = tmp_path / f"{Artifact.MICROSERVICES_LOGS}.inventory-worker.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_log_line_has_base_fields_and_correlation(tmp_path, logger):
    logger.info(
        "reservation request handled: %s",
        "reserved",
        extra=correlated(task_id="TASK_000001", message_id="MSG_1", event_seq=2, redelivered=False),
    )

    [record] = _records(tmp_path)
    assert re.fullmatch(ISO_UTC_MS, record["timestamp"])
    assert record["level"] == "INFO"
    assert record["service"] == "inventory-worker"
    assert record["execution_id"] == "PILOT_TEST"
    assert record["message"] == "reservation request handled: reserved"
    assert record["task_id"] == "TASK_000001"
    assert record["event_seq"] == 2
    assert record["redelivered"] is False


def test_unknown_extras_and_missing_fields_are_not_written(tmp_path, logger):
    logger.info("hello", extra=correlated(password="x"))

    [record] = _records(tmp_path)
    assert "password" not in record
    assert "task_id" not in record


def test_enum_values_are_serialized_as_text(tmp_path, logger):
    logger.info("state", extra=correlated(outcome=TaskResult.TIMEOUT))

    assert _records(tmp_path)[0]["outcome"] == "timeout"


def test_exceptions_are_recorded(tmp_path, logger):
    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("failed", extra=correlated(task_id="TASK_000001"))

    [record] = _records(tmp_path)
    assert record["level"] == "ERROR"
    assert "ValueError: boom" in record["exception"]


def test_handler_is_installed_only_once(tmp_path, logger):
    install(logger, jsonl_log_handler(tmp_path, service="other", execution_id="PILOT_TEST"))
    logger.info("once")

    assert len(_records(tmp_path)) == 1


def test_attributes_injected_by_libraries_do_not_override_correlation(tmp_path, logger):
    # O Celery grava o UUID da tarefa Celery em record.task_id.
    class CeleryLikeFilter(logging.Filter):
        def filter(self, record):
            record.task_id = "9213e7c7-344f-40b8-9e2f-2794deffd706"
            return True

    logger.addFilter(CeleryLikeFilter())
    try:
        logger.info("handled", extra=correlated(task_id="TASK_000001"))
        logger.info("celery internal")
    finally:
        logger.filters.clear()

    handled, internal = _records(tmp_path)
    assert handled["task_id"] == "TASK_000001"
    assert "task_id" not in internal
