from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from claims_copilot.api.claims import router as claims_router
from claims_copilot.config import get_settings
from claims_copilot.db import init_db
from claims_copilot.logging import setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


def create_app(init_db_on_startup: bool = True) -> FastAPI:
    setup_logging()
    settings = get_settings()
    app = FastAPI(
        title="Insurance Claims Intake Copilot",
        lifespan=lifespan if init_db_on_startup else None,
    )
    if settings.environment == "development":
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.include_router(claims_router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "llm_provider": settings.llm_provider}

    return app
