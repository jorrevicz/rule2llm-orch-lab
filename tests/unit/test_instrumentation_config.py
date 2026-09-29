"""Coleta comum configurada com o mesmo intervalo em todas as fontes (D-10, D-26)."""

import re
from pathlib import Path

import yaml

from shared.config import load_experiment_config
from tests.unit.test_config import REPO_CONFIG

REPO = REPO_CONFIG.parents[1]
INTERVAL = load_experiment_config(REPO_CONFIG).metrics.sampling_interval_seconds


def _seconds(text: str) -> float:
    value, unit = re.fullmatch(r"([0-9.]+)(ms|s)", text).groups()
    return float(value) / (1000 if unit == "ms" else 1)


def test_prometheus_scrapes_every_source_at_the_sampling_interval():
    prometheus = yaml.safe_load((REPO / "config/prometheus/prometheus.yml").read_text())

    assert _seconds(prometheus["global"]["scrape_interval"]) == INTERVAL
    jobs = {job["job_name"]: job for job in prometheus["scrape_configs"]}
    assert set(jobs) == {"rabbitmq", "cadvisor", "node"}
    assert jobs["rabbitmq"]["metrics_path"] == "/metrics/per-object"  # métricas por fila
    assert all("scrape_interval" not in job for job in jobs.values())  # sem exceções


def test_cadvisor_and_rabbitmq_refresh_at_the_sampling_interval():
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text())
    rabbitmq_conf = (REPO / "config/rabbitmq/rabbitmq.conf").read_text()

    housekeeping = next(arg for arg in compose["services"]["cadvisor"]["command"] if "housekeeping_interval" in arg)
    assert _seconds(housekeeping.split("=")[1]) == INTERVAL
    stats = re.search(r"^collect_statistics_interval\s*=\s*(\d+)", rabbitmq_conf, re.M)
    assert int(stats.group(1)) / 1000 == INTERVAL


def test_collectors_are_not_wired_into_the_services():
    """O decisor não lê a coleta (RNF-029): nenhum serviço de negócio depende dela."""
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text())
    for name in ("orders-api", "orders-worker", "inventory-worker"):
        service = compose["services"][name]
        assert not {"prometheus", "cadvisor", "node-exporter"} & set(service.get("depends_on", {}))
        assert "prometheus" not in str(service.get("environment", {})).lower()
