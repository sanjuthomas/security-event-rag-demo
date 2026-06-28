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

from inst import __version__
from inst.auth_routes import router as auth_router
from inst.config import settings
from inst.database import close, connect
from inst.kafka_publisher import kafka_publisher
from inst.routes import router
from inst.security_ui_routes import (
    SECURITY_EVENTS_STATIC_DIR,
    security_event_ui_store,
)
from inst.security_ui_routes import (
    router as security_ui_router,
)
from inst.service_identity import service_identity
from inst.ui_routes import STATIC_DIR
from inst.ui_routes import router as ui_router

UI_STATIC_DIR = STATIC_DIR
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_telemetry("instruction-service", service_version=__version__)
    instrument_app(app)
    await connect()
    await kafka_publisher.start()
    await service_identity.login()
    await security_event_ui_store.connect()
    logger.info("instruction browser and security event monitor ready")
    yield
    await kafka_publisher.close()
    await close()
    shutdown_telemetry()


app = FastAPI(
    title="Instruction Lifecycle Manager",
    description="REST API for canonical cash wire settlement instruction lifecycle (ISO 20022)",
    version=__version__,
    lifespan=lifespan,
)

app.include_router(auth_router)
app.include_router(router, prefix=settings.api_prefix)
app.include_router(ui_router)
app.include_router(security_ui_router)
app.mount("/ui/static", StaticFiles(directory=UI_STATIC_DIR), name="ui-static")
app.mount(
    "/ui/security-events/static",
    StaticFiles(directory=SECURITY_EVENTS_STATIC_DIR),
    name="security-events-static",
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "UP"}


def run() -> None:
    uvicorn.run(
        "inst.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )


if __name__ == "__main__":
    run()
