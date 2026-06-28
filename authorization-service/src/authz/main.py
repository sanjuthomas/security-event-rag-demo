from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from telemetry import (
    configure_telemetry,
    get_logger,
    instrument_app,
    shutdown_telemetry,
)

from authz import __version__
from authz.auth_routes import router as auth_router
from authz.authorization_routes import router as authorization_router
from authz.config import settings
from authz.eligibility import EligibilityService
from authz.opa import OpaClient
from authz.ui_routes import STATIC_DIR
from authz.ui_routes import router as ui_router
from authz.user_directory import UserDirectory

logger = get_logger(__name__)

user_directory: UserDirectory | None = None
eligibility_service: EligibilityService | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global user_directory, eligibility_service

    configure_telemetry("authorization-service", service_version=__version__)
    instrument_app(app)

    user_directory = UserDirectory(settings.users_file)
    eligibility_service = EligibilityService(
        users=user_directory,
        opa=OpaClient(),
    )

    logger.info("authorization-service ready on port %s", settings.port)
    yield

    user_directory = None
    eligibility_service = None
    shutdown_telemetry()


app = FastAPI(
    title="Authorization Service",
    description="Policy evaluation via OPA — eligible approvers and authorization decisions",
    version=__version__,
    lifespan=lifespan,
)

app.include_router(auth_router)
app.include_router(authorization_router, prefix=settings.api_prefix)
app.include_router(ui_router)
app.mount("/ui/static", StaticFiles(directory=STATIC_DIR), name="ui-static")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "UP"}


def run() -> None:
    uvicorn.run(
        "authz.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    run()
