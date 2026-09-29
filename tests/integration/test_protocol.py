"""Protocolo completo (Tabela 14) com um cenário curto contra o Docker Compose (M7-T08).

Reseta o ambiente (`docker compose down -v`) e o deixa no ar ao fim, apontado para a
execução criada aqui (dado de piloto). Cenário de teste, fora de `config/scenarios/`:
6 pedidos a 2/s e a rota primária falhando em toda tentativa por 2 s.

    .venv/bin/python -m pytest -m integration tests/integration/test_protocol.py
"""

import csv
import json

import pytest
import yaml

from scripts.experiment.run_experiment import REQUIRED_ARTIFACTS, run_once
from shared.artifacts import execution_dir
from tests.unit.test_config import REPO_CONFIG

pytestmark = pytest.mark.integration

SHORT_SCENARIO = {
    "scenario_id": "protocol_check",
    "description": "Cenário curto do teste de integração do protocolo (não é cenário do experimento).",
    "workload": {"dataset": "datasets/orders_v1.json", "requests": 6, "rate_per_second": 2.0, "seed": 1042},
    "fault": {
        "type": "intermittent_error", "target": "inventory.primary", "start_after_seconds": 1.0,
        "duration_seconds": 2.0, "failure_probability": 1.0, "delay_ms": None,
        "overload_rate_per_second": None, "seed": 1,
    },
}
CONSOLIDATED = ("latency_metrics.csv", "throughput_metrics.csv", "error_metrics.csv", "recovery_metrics.csv", "blast_radius.csv", "metrics_summary.csv")


def test_one_execution_follows_the_protocol_and_produces_every_artifact(tmp_path):
    (tmp_path / "protocol_check.yml").write_text(yaml.safe_dump(SHORT_SCENARIO), encoding="utf-8")

    metadata = run_once("protocol_check", "RULES", 1, scenarios_dir=tmp_path, settle_timeout_s=120)

    directory = execution_dir(REPO_CONFIG.parents[1] / "data", metadata["execution_id"])
    assert metadata["run_status"] == "VALID", metadata["invalid_reason"]
    assert metadata["readiness_status"] == "PASS"
    assert all(check["ok"] for check in metadata["readiness_checks"])
    assert metadata["phase"] == "PILOT" and metadata["eligible_for_sample"] is False
    assert metadata["software"]["python_version"] and metadata["software"]["rabbitmq_version"]
    for name in (*REQUIRED_ARTIFACTS, *CONSOLIDATED):
        assert (directory / name).stat().st_size > 0, name

    faults = [json.loads(line) for line in (directory / "fault_events.jsonl").read_text().splitlines()]
    assert {"FAULT_STARTED", "FAULT_APPLIED", "FAULT_ENDED"} <= {event["event_type"] for event in faults}
    [summary] = list(csv.DictReader((directory / "metrics_summary.csv").open()))
    assert summary["tasks"] == "6" and summary["run_status"] == "VALID"
    queues = {row["queue"] for row in csv.DictReader((directory / "queue_metrics.csv").open())}
    assert queues == {"inventory.primary", "inventory.fallback", "orders.events", "tasks.dlq"}
    components = {row["component"] for row in csv.DictReader((directory / "container_stats.csv").open())}
    assert {"orders-api", "orders-worker", "inventory-worker", "rabbitmq", "host", "ollama-host"} <= components
