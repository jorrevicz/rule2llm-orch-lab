"""Utilitários dos scripts de piloto para inspecionar o ambiente Docker Compose.

Somente leitura do ambiente (HTTP da API, filas via rabbitmqctl, SQLite via
container efêmero); nenhum script de piloto produz dado de amostra.
"""

import json
import os
import subprocess
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"
DEFAULT_BASE_URL = "http://localhost:8000"
QUEUES = ("inventory.primary", "inventory.fallback", "orders.events", "tasks.dlq")
TERMINAL_ORDER_STATUSES = {"COMPLETED", "FAILED"}
POLL_INTERVAL_SECONDS = 0.2

ORDERS_DB = ("orders-api", "/data/orders.db")
INVENTORY_DB = ("inventory-worker", "/data/inventory.db")

_SQLITE_QUERY_SCRIPT = """
import json, sqlite3, sys
connection = sqlite3.connect(sys.argv[1])
cursor = connection.execute(sys.argv[2], sys.argv[3:])
columns = [column[0] for column in cursor.description]
print(json.dumps({"columns": columns, "rows": cursor.fetchall()}))
"""


class InspectionError(RuntimeError):
    pass


def compose(*args: str, env: dict[str, str] | None = None) -> str:
    """`env` acrescenta variáveis ao ambiente do compose (ex.: `EXECUTION_ID`)."""
    command = ["docker", "compose", "-f", str(COMPOSE_FILE), *args]
    process_env = None if env is None else {**os.environ, **env}
    completed = subprocess.run(command, capture_output=True, text=True, env=process_env)
    if completed.returncode != 0:
        raise InspectionError(f"{' '.join(args[:3])}: {completed.stderr.strip()}")
    return completed.stdout


def query_sqlite(database: tuple[str, str], sql: str, *params: str) -> list[list]:
    """Consulta o SQLite de um serviço num container efêmero (`run --no-deps`).

    Funciona mesmo com o container do serviço parado, pois usa o mesmo volume.
    """
    return _run_query(database, sql, *params)["rows"]


def query_sqlite_records(database: tuple[str, str], sql: str, *params: str) -> list[dict]:
    """Como `query_sqlite`, mas cada linha vem como dicionário coluna → valor."""
    result = _run_query(database, sql, *params)
    return [dict(zip(result["columns"], row)) for row in result["rows"]]


def _run_query(database: tuple[str, str], sql: str, *params: str) -> dict:
    service, path = database
    output = compose(
        "run", "--rm", "--no-deps", "-T", service,
        "python", "-c", _SQLITE_QUERY_SCRIPT, path, sql, *params,
    )
    return json.loads(output)


def scalar(database: tuple[str, str], sql: str, *params: str):
    [[value]] = query_sqlite(database, sql, *params)
    return value


def queue_consumers() -> dict[str, int]:
    output = compose(
        "exec", "-T", "rabbitmq",
        "rabbitmqctl", "list_queues", "name", "consumers", "--formatter", "json", "-q",
    )
    return {queue["name"]: queue["consumers"] for queue in json.loads(output)}


def queue_depths() -> dict[str, int]:
    # rabbitmqctl em vez da API de management: as contagens da API têm atraso.
    output = compose(
        "exec", "-T", "rabbitmq",
        "rabbitmqctl", "list_queues", "name", "messages", "--formatter", "json", "-q",
    )
    return {queue["name"]: queue["messages"] for queue in json.loads(output)}


def request_json(url: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, headers={"content-type": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def create_order(base_url: str, items: list[dict]) -> dict:
    return request_json(f"{base_url}/orders", {"items": items})


def wait_until(condition: Callable[[], bool], timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while True:
        if condition():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_INTERVAL_SECONDS)


def wait_for_terminal_status(base_url: str, order_id: str, timeout_s: float) -> str:
    status = "UNKNOWN"

    def terminal() -> bool:
        nonlocal status
        status = request_json(f"{base_url}/orders/{order_id}")["status"]
        return status in TERMINAL_ORDER_STATUSES

    wait_until(terminal, timeout_s)
    return status


def wait_for_drained_queues(timeout_s: float) -> dict[str, int]:
    # O ack (acks_late) acontece logo após o processamento; pode haver alguns
    # milissegundos entre o efeito aparecer no banco e a fila zerar.
    depths: dict[str, int] = {}

    def drained() -> bool:
        nonlocal depths
        depths = queue_depths()
        return not any(depths.get(name, 0) for name in QUEUES)

    wait_until(drained, timeout_s)
    return depths
