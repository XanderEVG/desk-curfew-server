import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi.staticfiles import StaticFiles
from starlette.requests import Request
from starlette.responses import RedirectResponse

from app.api.routers import auth, control, health, pcs, stats
from app.config import get_settings
from app.core.mqtt_client import MqttService
from app.core.scheduler import run_scheduler
from app.fastapi_app import CurfewApp
from app.web.router import LoginRequiredError
from app.web.router import router as web_router

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: CurfewApp):
    settings = get_settings()

    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("app.core.mqtt_client").setLevel(logging.DEBUG)

    mqtt_service = MqttService(settings)
    await mqtt_service.start()

    scheduler_task = asyncio.create_task(run_scheduler(settings, mqtt_service))

    app.mqtt = mqtt_service
    app.settings = settings

    logger.info("desk-curfew-server started")

    try:
        yield
    finally:
        scheduler_task.cancel()

        with suppress(asyncio.CancelledError):
            await scheduler_task

        await mqtt_service.stop()
        logger.info("desk-curfew-server stopped")


app = CurfewApp(
    title="desk-curfew-server",
    version="0.1.0",
    lifespan=lifespan,
    swagger_ui_parameters={"persistAuthorization": True},
)

app.include_router(health.router)
app.include_router(pcs.router)
app.include_router(control.router)
app.include_router(auth.router)
app.include_router(stats.router)
app.include_router(web_router)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.exception_handler(LoginRequiredError)
async def login_required_handler(request: Request, exc: LoginRequiredError):
    return RedirectResponse("/login", status_code=303)
