"""Readiness automática antes da janela de medição (M7-T04; metodologia §4.4.1, Tabela 14 etapa 4).

    .venv/bin/python -m scripts.experiment.readiness --execution-id PILOT_0050

Critérios da metodologia: (a) containers necessários ativos; (b) filas declaradas;
(c) microserviços respondendo ao health check; (d) bancos SQLite no estado inicial;
(e) coletores de métricas ativos; (f) na abordagem LLM, modelo carregado e warm-up
concluído — o (f) é a readiness do LLM (`scripts/pilot/llm_readiness.py`), etapa 5.

Acréscimos do piloto (M6-T10): os serviços apontam para esta execução, e o host está
no estado controlado — no macOS, na tomada, sem Low Power Mode e com sleep impedido
(asserção do `caffeinate` do executor); no Linux (servidor da coleta), não se aplica.

Qualquer critério reprovado → `readiness_status = FAIL` → a execução não é válida.
"""

import argparse
import json
import platform
import subprocess
import sys
import tempfile
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from scripts.datasets.generate_dataset import REPO_ROOT
from scripts.experiment.reset_environment import _SNAPSHOT_SCRIPT, INITIAL_STATE_FILE
from scripts.pilot.environment import DEFAULT_BASE_URL, QUEUES, compose
from services.inventory.app.db.connection import init_database as init_inventory_database
from services.orders.app.db.connection import init_database as init_orders_database
from shared.artifacts import execution_dir
from shared.catalog import load_catalog
from shared.config import load_experiment_config
from shared.timestamps import utc_now_iso

DEFAULT_DATA_ROOT = REPO_ROOT / "data"
PROMETHEUS_URL = "http://localhost:9090"
REQUIRED_SERVICES = ("rabbitmq", "orders-api", "orders-worker", "inventory-worker", "prometheus", "cadvisor", "node-exporter")
EXECUTION_SERVICES = ("orders-api", "orders-worker", "inventory-worker")
PROMETHEUS_JOBS = {"rabbitmq", "cadvisor", "node"}


@dataclass
class Check:
    name: str
    ok: bool
    detail: object = None


@dataclass
class Readiness:
    status: str
    timestamp: str
    checks: list[Check] = field(default_factory=list)

    @property
    def failures(self) -> list[str]:
        return [f"{check.name}: {check.detail}" for check in self.checks if not check.ok]


def _http_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as response:
        return json.load(response)


def check_containers() -> Check:
    rows = [json.loads(line) for line in compose("ps", "--format", "json").splitlines() if line.strip()]
    states = {row["Service"]: (row["State"], row.get("Health") or "") for row in rows}
    bad = {
        name: states.get(name, ("missing", ""))
        for name in REQUIRED_SERVICES
        if states.get(name, ("missing", ""))[0] != "running" or states[name][1] not in {"", "healthy"}
    }
    return Check("containers", not bad, bad or sorted(states))


def check_execution(execution_id: str) -> Check:
    running = {
        service: compose("exec", "-T", service, "printenv", "EXECUTION_ID").strip() for service in EXECUTION_SERVICES
    }
    return Check("services_on_execution", set(running.values()) == {execution_id}, running)


def check_queues(state: dict) -> Check:
    messages, consumers = state["queues"]["messages"], state["queues"]["consumers"]
    problems = {
        queue: {"messages": messages.get(queue), "consumers": consumers.get(queue)}
        for queue in QUEUES
        if queue not in messages or messages[queue] != 0 or consumers.get(queue, 0) < 1
    }
    return Check("queues_declared_empty_with_consumers", not problems, problems or consumers)


def check_api_health(base_url: str = DEFAULT_BASE_URL) -> Check:
    try:
        body = _http_json(f"{base_url}/health")
    except OSError as error:
        return Check("api_health", False, repr(error))
    return Check("api_health", body.get("status") == "ok", body)


def expected_database_state(catalog_path: Path) -> dict:
    """Bancos recém-criados pelo mesmo código: a referência do estado inicial."""
    snapshots = {}
    with tempfile.TemporaryDirectory() as tmp:
        paths = {"orders": Path(tmp) / "orders.db", "inventory": Path(tmp) / "inventory.db"}
        init_orders_database(paths["orders"])
        init_inventory_database(paths["inventory"], load_catalog(catalog_path))
        for name, path in paths.items():
            output = subprocess.run([sys.executable, "-c", _SNAPSHOT_SCRIPT, str(path)], capture_output=True, text=True, check=True)
            snapshots[name] = json.loads(output.stdout)
    return snapshots


def check_databases(state: dict, expected: dict) -> Check:
    diverging = {
        name: {"observed": state["databases"].get(name), "expected": snapshot}
        for name, snapshot in expected.items()
        if state["databases"].get(name, {}).get("logical_sha256") != snapshot["logical_sha256"]
    }
    return Check("databases_initial_state", not diverging, diverging or {n: s["logical_sha256"][:12] for n, s in expected.items()})


def check_collectors(prometheus_url: str = PROMETHEUS_URL) -> Check:
    try:
        targets = _http_json(f"{prometheus_url}/api/v1/targets")["data"]["activeTargets"]
    except OSError as error:
        return Check("collectors", False, repr(error))
    health = {target["labels"]["job"]: target["health"] for target in targets}
    ok = set(health) == PROMETHEUS_JOBS and all(value == "up" for value in health.values())
    return Check("collectors", ok, health)


def _command(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True).stdout


def check_host(system: str = platform.system(), run: Callable[..., str] = _command) -> Check:
    """Estado de energia do host (achado M6-T10). Só o macOS do piloto tem esse risco."""
    if system != "Darwin":
        return Check("host_power", True, {"platform": system, "power_checks": "not_applicable"})
    battery, settings, assertions = run("pmset", "-g", "batt"), run("pmset", "-g"), run("pmset", "-g", "assertions")
    detail = {
        "platform": system,
        "ac_power": "AC Power" in battery,
        "low_power_mode_off": any(line.split() == ["lowpowermode", "0"] for line in settings.splitlines()),
        "sleep_prevented": any(
            line.split()[:2] == ["PreventUserIdleSystemSleep", "1"] for line in assertions.splitlines()
        ),
    }
    return Check("host_power", all(value for key, value in detail.items() if key != "platform"), detail)


def check(execution_id: str, data_root: Path = DEFAULT_DATA_ROOT) -> Readiness:
    directory = execution_dir(data_root, execution_id)
    config = load_experiment_config(directory / "experiment_config.yml")
    state = json.loads((directory / INITIAL_STATE_FILE).read_text(encoding="utf-8"))
    checks = [
        check_containers(),
        check_execution(execution_id),
        check_queues(state),
        check_api_health(),
        check_databases(state, expected_database_state(REPO_ROOT / config.inventory.catalog)),
        check_collectors(),
        check_host(),
    ]
    status = "PASS" if all(item.ok for item in checks) else "FAIL"
    return Readiness(status=status, timestamp=utc_now_iso(), checks=checks)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--execution-id", required=True)
    args = parser.parse_args()
    readiness = check(args.execution_id)
    print(json.dumps(asdict(readiness), indent=2, ensure_ascii=False, default=str))
    return 0 if readiness.status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
