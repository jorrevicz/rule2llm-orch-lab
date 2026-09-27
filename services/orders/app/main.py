"""Aplicação FastAPI do orders-service."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from services.orders.app.api.orders import router as orders_router
from services.orders.app.db.connection import init_database
from services.orders.app.settings import Settings, load_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        init_database(resolved.database_path)
        yield

    app = FastAPI(title="orders-service", lifespan=lifespan)
    app.state.settings = resolved
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


app = create_app()
