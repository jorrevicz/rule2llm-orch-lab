import json
import sqlite3

import pytest

from services.orders.app.db.connection import connect, init_database
from services.orders.app.db.repositories import create_order_with_task
from services.orders.app.orchestration.executor import DecisionExecutor
from shared.decision import (
    Action,
    Decision,
    ProposedDecision,
    ReasonCode,
    ValidationErrorCode,
    ValidationResult,
    resolve_action,
)

TASK = "TASK_000001"


@pytest.fixture
def connection(tmp_path) -> sqlite3.Connection:
    path = tmp_path / "orders.db"
    init_database(path)
    conn = connect(path)
    create_order_with_task(conn, execution_id="PILOT_TEST", items_json="[]", now="2026-09-28T12:00:00.000Z")
    yield conn
    conn.close()


def _status(connection) -> tuple[str, str]:
    row = connection.execute(
        "SELECT t.status, o.status FROM tasks t JOIN orders o ON o.order_id = t.order_id"
    ).fetchone()
    return tuple(row)


def _last_event(connection) -> sqlite3.Row:
    return connection.execute("SELECT * FROM task_events ORDER BY event_id DESC LIMIT 1").fetchone()


ABORT = Decision(action=Action.ABORT, target=None, reason_code=ReasonCode.ATTEMPTS_EXHAUSTED)


def test_abort_ends_task_and_fails_order(connection):
    result = DecisionExecutor().execute(connection, ABORT, task_id=TASK, decision_id="DEC_1")

    assert result.action == Action.ABORT and result.changed_state
    assert _status(connection) == ("ABORTED", "FAILED")


def test_abort_is_recorded_in_the_trajectory(connection):
    DecisionExecutor().execute(connection, ABORT, task_id=TASK, decision_id="DEC_1")

    event = _last_event(connection)
    assert (event["event_seq"], event["event_type"], event["service"]) == (2, "TASK_ABORTED", "orders")
    assert json.loads(event["payload_json"]) == {"decision_id": "DEC_1", "reason_code": "ATTEMPTS_EXHAUSTED"}


def test_abort_of_a_terminal_task_changes_nothing(connection):
    executor = DecisionExecutor()
    executor.execute(connection, ABORT, task_id=TASK, decision_id="DEC_1")

    result = executor.execute(connection, ABORT, task_id=TASK, decision_id="DEC_2")

    assert not result.changed_state
    assert _last_event(connection)["event_seq"] == 2


def test_invalid_decision_is_executed_as_abort_invalid_decision(connection):
    # Política obrigatória: decisão inválida → registrar → ABORT (CLAUDE §23).
    proposal = ProposedDecision(action="RETRY", target="service_c", reason_code="RETRY")
    executed = resolve_action(proposal, ValidationResult.invalid(ValidationErrorCode.UNKNOWN_TARGET))

    DecisionExecutor().execute(connection, executed, task_id=TASK, decision_id="DEC_1")

    assert _status(connection) == ("ABORTED", "FAILED")
    assert json.loads(_last_event(connection)["payload_json"])["reason_code"] == "INVALID_DECISION"
