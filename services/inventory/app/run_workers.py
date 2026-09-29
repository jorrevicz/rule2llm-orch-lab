"""Processo principal do container do inventory-service (D-21).

    python -m services.inventory.app.run_workers

Sobe um worker Celery por rota lógica, no mesmo container e com o mesmo
`inventory.db`: `inventory.primary` e `inventory.fallback`. Uma degradação da rota
primária (atraso, falha injetada) não bloqueia o fallback; parar o container derruba
as duas rotas — indisponibilidade total do serviço (metodologia §4.4).

Se um dos workers terminar, o outro é encerrado e o container sai: o serviço nunca
fica "meio de pé" sem registro. SIGTERM/SIGINT são repassados aos dois (parada limpa
pelo `docker compose stop`).
"""

import os
import signal
import subprocess
import sys
import time

from services.inventory.app.db.connection import init_database
from services.inventory.app.settings import load_settings
from shared.catalog import load_catalog
from shared.config import load_experiment_config
from shared.messaging import Route

# papel do processo (nome dos arquivos de artefatos) → fila consumida
ROUTE_WORKERS = {
    "inventory-primary": Route.INVENTORY_PRIMARY,
    "inventory-fallback": Route.INVENTORY_FALLBACK,
}
POLL_SECONDS = 0.5


def worker_command(role: str, queue: str) -> list[str]:
    return [
        sys.executable, "-m", "celery",
        "--app=services.inventory.app.messaging.celery_app",
        "worker",
        f"--queues={queue}",
        "--concurrency=1",
        f"--hostname={role}@%h",
        # Sem coordenação entre workers: mingle/gossip/heartbeat usam filas transitórias
        # (bloqueadas no RabbitMQ 4) e não fazem parte do experimento.
        "--without-mingle",
        "--without-gossip",
        "--without-heartbeat",
        "--loglevel=INFO",
    ]


def main() -> int:
    settings = load_settings()
    # Esquema e catálogo criados uma vez, antes de os dois workers abrirem o banco.
    init_database(settings.database_path, load_catalog(load_experiment_config().inventory.catalog))
    workers = [
        subprocess.Popen(worker_command(role, str(queue)), env={**os.environ, "SERVICE_ROLE": role})
        for role, queue in ROUTE_WORKERS.items()
    ]

    def forward(signum: int, _frame: object) -> None:
        for worker in workers:
            if worker.poll() is None:
                worker.send_signal(signum)

    signal.signal(signal.SIGTERM, forward)
    signal.signal(signal.SIGINT, forward)

    while all(worker.poll() is None for worker in workers):
        time.sleep(POLL_SECONDS)
    forward(signal.SIGTERM, None)
    codes = [worker.wait() for worker in workers]
    return next((code for code in codes if code), 0)


if __name__ == "__main__":
    sys.exit(main())
