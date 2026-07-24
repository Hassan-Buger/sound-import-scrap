import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.router import router
from app.config import settings

# Ensure scraper loggers are visible when running under uvicorn,
# which defaults the root logger to WARNING level by default.
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    force=True,
)

logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.database import engine, Base

    # Log (with credentials stripped) what we're actually trying to connect
    # to, so misconfigured env vars are obvious in the deploy logs instead
    # of a bare "Connection refused".
    safe_url = settings.database_url.split("@")[-1] if "@" in settings.database_url else settings.database_url
    logger.info("Connecting to database at: %s", safe_url)

    if "localhost" in settings.database_url or "127.0.0.1" in settings.database_url:
        logger.warning(
            "DATABASE_URL is pointing at localhost. If this is running on "
            "Railway (or any host), this almost always means the "
            "DATABASE_URL environment variable was not set on this service "
            "and the hardcoded local default is being used instead."
        )

    last_exc = None
    for attempt in range(1, 6):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning(
                "Database not ready yet (attempt %s/5): %s", attempt, exc
            )
            await asyncio.sleep(3)
    else:
        logger.error(
            "Could not connect to the database after 5 attempts. Check that "
            "DATABASE_URL is set correctly on this service and that the "
            "database service is running and reachable."
        )
        raise last_exc

    yield
    await engine.dispose()


app = FastAPI(
    title="SoundImports Scraper API",
    description="Stable REST API for normalized product data from SoundImports.eu. "
    "Provides categories, brands, products, stats, and sync trigger. "
    "This API is the stable contract for the WordPress plugin.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/health")
async def health():
    return {"status": "ok"}
