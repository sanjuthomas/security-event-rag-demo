import asyncio
import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from instruction_lifecycle_manager import __version__
from instruction_lifecycle_manager.config import settings
from instruction_lifecycle_manager.database import close, connect
from instruction_lifecycle_manager.kafka_publisher import kafka_publisher
from instruction_lifecycle_manager.routes import router
from instruction_lifecycle_manager.security_event_watcher import SecurityEventWatcher
from instruction_lifecycle_manager.security_ui_routes import (
    SECURITY_EVENTS_STATIC_DIR,
    security_event_broadcaster,
    security_event_ui_store,
    router as security_ui_router,
)
from instruction_lifecycle_manager.ui_routes import STATIC_DIR, instruction_broadcaster, router as ui_router
from instruction_lifecycle_manager.ui_watcher import InstructionWatcher

UI_STATIC_DIR = STATIC_DIR
logger = logging.getLogger(__name__)
_instruction_watch_task: asyncio.Task | None = None
_security_event_watch_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _instruction_watch_task, _security_event_watch_task
    logging.basicConfig(level=logging.INFO)
    await connect()
    await kafka_publisher.start()
    await security_event_ui_store.connect()

    instruction_watcher = InstructionWatcher()
    await instruction_watcher.connect()
    _instruction_watch_task = asyncio.create_task(
        instruction_watcher.watch(instruction_broadcaster)
    )

    security_event_watcher = SecurityEventWatcher(security_event_ui_store)
    await security_event_watcher.connect()
    _security_event_watch_task = asyncio.create_task(
        security_event_watcher.watch(security_event_broadcaster)
    )

    logger.info("instruction browser and security event monitor live feeds started")
    yield

    for task in (_instruction_watch_task, _security_event_watch_task):
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    await kafka_publisher.close()
    await close()


app = FastAPI(
    title="Instruction Lifecycle Manager",
    description="REST API for canonical cash wire settlement instruction lifecycle (ISO 20022)",
    version=__version__,
    lifespan=lifespan,
)

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
        "instruction_lifecycle_manager.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )


if __name__ == "__main__":
    run()
