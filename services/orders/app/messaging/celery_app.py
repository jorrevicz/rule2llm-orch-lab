"""Aplicação Celery do orders-service.

Publica em inventory.* e consome orders.events.
"""

import logging
from functools import cache

from celery import Celery
from celery.signals import after_setup_logger, after_setup_task_logger, worker_init

from services.orders.app.db.connection import init_database
from services.orders.app.messaging.dead_letter_consumer import build_dead_letter_step
from services.orders.app.settings import load_settings
from shared.messaging import configure_celery
from shared.structured_logging import JsonlLogHandler, install, jsonl_log_handler

settings = load_settings()

app = Celery("orders-service", include=["services.orders.app.messaging.consumers"])
# Prefetch sem limite: tarefas internas com atraso não podem reter os eventos (ver
# shared.messaging.configure_celery).
configure_celery(app, settings.broker_url, prefetch_multiplier=0)


# D-07: consumidor bruto da tasks.dlq (só no worker; a API não inicia bootsteps).
app.steps["consumer"].add(build_dead_letter_step(settings))


@worker_init.connect
def _prepare_database(**_: object) -> None:
    init_database(settings.database_path)


@cache
def _log_handler() -> JsonlLogHandler:
    return jsonl_log_handler(
        settings.artifacts_dir, service=settings.service_role, execution_id=settings.execution_id
    )


@after_setup_logger.connect
@after_setup_task_logger.connect
def _structured_logs(logger: logging.Logger, **_: object) -> None:
    # O Celery desliga a propagação do logger de tarefas: o handler vai nos dois.
    install(logger, _log_handler())
