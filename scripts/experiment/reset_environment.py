"""Restaura o estado inicial do ambiente (M7-T03; metodologia §4.4.1, Tabela 14 etapas 2–3).

    .venv/bin/python -m scripts.experiment.reset_environment --execution-id PILOT_0050

1. `docker compose down -v`: para tudo e apaga os volumes — `orders.db`, `inventory.db`
   e a base do Prometheus; o RabbitMQ é recriado a partir de `definitions.json`, com
   filas e DLQ vazias (estados transitórios da execução anterior não sobrevivem);
2. reconstrói as imagens dos serviços (cache do Docker) com o commit atual no rótulo
   `org.opencontainers.image.revision` e sobe o ambiente já apontado para a execução
   (`EXECUTION_ID`, `EXECUTION_CONFIG_PATH`), esperando os healthchecks; os bancos são
   criados vazios, com o catálogo do Inventory. A readiness confere o rótulo: nenhuma
   execução roda com imagem de outro commit;
3. registra o estado inicial em `initial_state.json` (hash lógico e contagem de cada
   tabela dos dois SQLite, filas e consumidores) e os passos em `reset.log`.

O hash é do conteúdo lógico (esquema + linhas ordenadas), não do arquivo: o arquivo do
SQLite muda com o WAL sem mudar o conteúdo. A verificação do estado esperado é da
readiness (`readiness.py`).
"""

import argparse
import json
import sys
from pathlib import Path

from scripts.pilot.environment import COMPOSE_FILE, REPO_ROOT, compose, queue_consumers, queue_depths
from scripts.pilot.new_execution import git_commit
from shared.artifacts import execution_dir
from shared.timestamps import utc_now_iso

DEFAULT_DATA_ROOT = REPO_ROOT / "data"
CONTAINER_DATA_ROOT = Path("/srv/data")
INITIAL_STATE_FILE = "initial_state.json"
RESET_LOG_FILE = "reset.log"
DATABASES = {"orders": ("orders-api", "/data/orders.db"), "inventory": ("inventory-worker", "/data/inventory.db")}

# Executado dentro do container do serviço dono do banco (sem acesso cruzado).
_SNAPSHOT_SCRIPT = """
import hashlib, json, sqlite3, sys
connection = sqlite3.connect(sys.argv[1])
tables = [row[0] for row in connection.execute(
    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
schema = [row[0] for row in connection.execute(
    "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type, name")]
snapshot = {"tables": {}}
digest = hashlib.sha256(json.dumps(schema).encode())
for table in tables:
    rows = connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
    snapshot["tables"][table] = {"rows": len(rows)}
    digest.update(json.dumps([table, rows], default=str).encode())
snapshot["logical_sha256"] = digest.hexdigest()
print(json.dumps(snapshot))
"""


class ResetLog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def __call__(self, message: str) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(f"{utc_now_iso()} {message}\n")


def head_commit() -> str:
    return (git_commit() or "unknown").removesuffix("-dirty")


def execution_env(execution_id: str, data_root: Path = DEFAULT_DATA_ROOT) -> dict[str, str]:
    directory = execution_dir(data_root, execution_id)
    container_config = CONTAINER_DATA_ROOT / directory.relative_to(data_root) / "experiment_config.yml"
    return {
        "EXECUTION_ID": execution_id,
        "EXECUTION_CONFIG_PATH": str(container_config),
        "GIT_COMMIT": head_commit(),
    }


def database_snapshot(service: str, path: str) -> dict:
    return json.loads(compose("exec", "-T", service, "python", "-c", _SNAPSHOT_SCRIPT, path))


def capture_initial_state() -> dict:
    return {
        "captured_at": utc_now_iso(),
        "databases": {name: database_snapshot(*location) for name, location in DATABASES.items()},
        "queues": {"messages": queue_depths(), "consumers": queue_consumers()},
    }


def reset_environment(execution_id: str, data_root: Path = DEFAULT_DATA_ROOT, *, build: bool = True) -> dict:
    directory = execution_dir(data_root, execution_id)
    if not (directory / "experiment_config.yml").is_file():
        raise SystemExit(f"execution {execution_id} has no effective config; open it first")
    log = ResetLog(directory / RESET_LOG_FILE)
    log(f"reset started for {execution_id} ({COMPOSE_FILE.name})")
    compose("down", "-v", "--remove-orphans")
    log("docker compose down -v --remove-orphans: containers, bancos, filas e métricas removidos")
    up = ["up", "-d", "--wait", *(["--build"] if build else [])]
    compose(*up, env=execution_env(execution_id, data_root))
    log(f"docker compose {' '.join(up)}: ambiente no ar para {execution_id}")
    state = capture_initial_state()
    (directory / INITIAL_STATE_FILE).write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    log(f"estado inicial registrado em {INITIAL_STATE_FILE}")
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--execution-id", required=True)
    args = parser.parse_args()
    print(json.dumps(reset_environment(args.execution_id), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
