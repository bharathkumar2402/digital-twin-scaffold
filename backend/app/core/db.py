from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(settings.app_database_url, pool_pre_ping=True)

async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


# Separate engine for the TimescaleDB instance (sensor_readings only) — a physically
# different database from the one above, see app/core/config.py.
timescale_engine = create_async_engine(settings.app_timescale_url, pool_pre_ping=True)

timescale_session_factory = async_sessionmaker(timescale_engine, expire_on_commit=False)


async def get_timescale_session() -> AsyncGenerator[AsyncSession, None]:
    async with timescale_session_factory() as session:
        yield session
