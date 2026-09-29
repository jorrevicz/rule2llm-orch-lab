"""Aplicação Celery do inventory-service.

Consome inventory.primary / inventory.fallback e publica em orders.events.
"""

import logging
from functools import cache

from celery import Celery
from celery.signals import after_setup_logger, after_setup_task_logger, worker_init

from services.inventory.app.db.connection import init_database
from services.inventory.app.settings import load_settings
from shared.catalog import load_catalog
from shared.config import ExperimentConfig, load_experiment_config
from shared.messaging import configure_celery
from shared.structured_logging import JsonlLogHandler, install, jsonl_log_handler

settings = load_settings()

app = Celery("inventory-service", include=["services.inventory.app.messaging.consumers"])
configure_celery(app, settings.broker_url)


@cache
def experiment_config() -> ExperimentConfig:
    return load_experiment_config()


@worker_init.connect
def _prepare_database(**_: object) -> None:
    init_database(settings.database_path, load_catalog(experiment_config().inventory.catalog))


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
