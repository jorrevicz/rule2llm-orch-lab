"""Aplicação FastAPI do orders-service.

Servida pela fábrica (`uvicorn --factory services.orders.app.main:create_app`): importar
este módulo não lê configuração nem abre recursos.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from services.orders.app.api.orders import router as orders_router
from services.orders.app.db.connection import init_database
from services.orders.app.messaging.celery_app import app as celery_app
from services.orders.app.orchestration.coordination import Coordination, build_coordination
from services.orders.app.settings import Settings, load_settings
from shared.config import load_experiment_config
from shared.structured_logging import install, jsonl_log_handler, uninstall


def create_app(
    settings: Settings | None = None, coordination: Coordination | None = None
) -> FastAPI:
    resolved = settings or load_settings()
    resolved_coordination = coordination or build_coordination(
        resolved, load_experiment_config(), celery_app
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        init_database(resolved.database_path)
        root = logging.getLogger()
        install(
            root,
            jsonl_log_handler(
                resolved.artifacts_dir,
                service=resolved.service_role,
                execution_id=resolved.execution_id,
            ),
        )
        yield
        uninstall(root)

    app = FastAPI(title="orders-service", lifespan=lifespan)
    app.state.settings = resolved
    app.state.coordination = resolved_coordination
    app.include_router(orders_router)
    app.add_exception_handler(RequestValidationError, _invalid_request)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


async def _invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Pedido inválido → 400 (docs/07 §7.3), em vez do 422 padrão do FastAPI.
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": jsonable_encoder(exc.errors())},
    )

