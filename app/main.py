import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routers import control, health, pcs
from app.config import get_settings
from app.core.mqtt_client import MqttService
from app.core.scheduler import run_scheduler

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    mqtt_service = MqttService(settings)
    await mqtt_service.start()

    scheduler_task = asyncio.create_task(run_scheduler(settings, mqtt_service))

    app.state.mqtt = mqtt_service
    app.state.settings = settings

    logger.info("desk-curfew-server started")

    try:
        yield
    finally:
        scheduler_task.cancel()

        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass

        await mqtt_service.stop()
        logger.info("desk-curfew-server stopped")


app = FastAPI(
    title="desk-curfew-server",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(pcs.router)
app.include_router(control.router)
app.include_router(auth.router)


@app.get("/")
async def root() -> dict:
    return {
        "docs": "/docs",
        "health": "/health",
        "pcs": "/api/pcs",
    }