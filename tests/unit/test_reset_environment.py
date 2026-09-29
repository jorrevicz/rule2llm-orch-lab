"""Estado inicial reprodutível do ambiente (M7-T03; RF-037, RNF-024)."""

import json
import subprocess
import sys

from scripts.datasets.generate_dataset import CATALOG_PATH
from scripts.experiment.reset_environment import _SNAPSHOT_SCRIPT, execution_env
from services.inventory.app.db.connection import init_database
from shared.catalog import load_catalog


def snapshot(path) -> dict:
    output = subprocess.run([sys.executable, "-c", _SNAPSHOT_SCRIPT, str(path)], capture_output=True, text=True, check=True)
    return json.loads(output.stdout)


def test_fresh_databases_have_the_same_logical_hash_and_expected_counts(tmp_path):
    first, second = tmp_path / "a.db", tmp_path / "b.db"
    for path in (first, second):
        init_database(path, load_catalog(CATALOG_PATH))

    a, b = snapshot(first), snapshot(second)
    assert a == b
    assert a["tables"] == {"processed_messages": {"rows": 0}, "reservations": {"rows": 0}, "stock": {"rows": 50}}


def test_any_leftover_row_changes_the_hash(tmp_path):
    path = tmp_path / "inventory.db"
    init_database(path, load_catalog(CATALOG_PATH))
    clean = snapshot(path)

    import sqlite3

    connection = sqlite3.connect(path)
    connection.execute(
        "INSERT INTO processed_messages VALUES ('MSG_1', 'TASK_000001', 'STOCK_RESERVATION_REQUESTED', 'succeeded', '{}', 'x')"
    )
    connection.commit()
    connection.close()

    dirty = snapshot(path)
    assert dirty["logical_sha256"] != clean["logical_sha256"]
    assert dirty["tables"]["processed_messages"] == {"rows": 1}


def test_services_start_pointed_at_the_execution(tmp_path):
    env = execution_env("PILOT_0042", tmp_path)

    assert env["EXECUTION_ID"] == "PILOT_0042"
    assert env["EXECUTION_CONFIG_PATH"] == "/srv/data/pilot/PILOT_0042/experiment_config.yml"
    assert len(env["GIT_COMMIT"]) == 40 and not env["GIT_COMMIT"].endswith("-dirty")
