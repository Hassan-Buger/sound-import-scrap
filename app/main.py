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
    from app.database import engine, async_session_factory
    from app.migrations import upgrade_database
    from app import crud

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
    for attempt in range(1, 4):
        try:
            await upgrade_database()
            async with async_session_factory() as session:
                interrupted = await crud.mark_running_jobs_interrupted(
                    session, settings.job_stale_after
                )
            if interrupted:
                logger.warning(
                    "Marked %d abandoned scrape job(s) interrupted and resumable",
                    interrupted,
                )
            logger.info("Database migrations applied successfully.")
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning(
                "Primary database not ready (attempt %s/3): %s", attempt, exc
            )
            await asyncio.sleep(2)
    else:
        # Falling back from PostgreSQL to a fresh local SQLite file creates a
        # split-brain deployment and makes persisted scrape progress disappear.
        raise RuntimeError(
            f"Database migration/connection failed after 3 attempts: {last_exc}"
        ) from last_exc

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


@app.get("/")
async def root():
    return {"status": "ok", "message": "SoundImports Scraper API is running", "docs": "/docs"}


@app.get("/health")
async def health():
    return {"status": "ok"}
