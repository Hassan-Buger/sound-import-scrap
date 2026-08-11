from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine, AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

_is_sqlite = settings.database_url.startswith("sqlite")

if _is_sqlite:
    engine = create_async_engine(
        settings.database_url,
        echo=settings.DB_ECHO,
    )
else:
    engine = create_async_engine(
        settings.database_url,
        echo=settings.DB_ECHO,
        # Bounded pool: a scrape can otherwise open well over 100 concurrent
        # asyncpg connections, each holding its own buffers, and OOM a small
        # container (Railway free/usage plans give ~512 MB).  10 +/- 5 keeps
        # the scrape fast enough while putting a hard ceiling on connection
        # memory.
        pool_size=10,
        max_overflow=5,
        pool_pre_ping=True,
        pool_use_lifo=True,
        pool_timeout=60,
    )

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()
