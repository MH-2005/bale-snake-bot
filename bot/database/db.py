import logging  # Fixed: missing import caused crash

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy import text

from bot.config import settings
from bot.database.models import Base

# Create async engine optimized for PostgreSQL
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,          # Set to True for SQL query debugging
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,  # Verify connections before each use
)

# Session factory used directly throughout the application
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """
    Initialize the database by creating all tables defined in models.
    Also adds the 'display_name' column if it does not exist (for existing databases).
    Safe to call multiple times.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Add display_name column to scores if missing (migration for existing DBs)
        await conn.execute(
            text(
                "ALTER TABLE scores ADD COLUMN IF NOT EXISTS display_name VARCHAR(500)"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE daily_winners ADD COLUMN IF NOT EXISTS display_name VARCHAR(500)"
            )
        )

    logging.info("✅ Database tables initialized successfully.")


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Async context manager that yields a database session.
    Handles commit on success, rollback on exception, and session closing.

    Usage:
        async with get_session() as session:
            session.add(...)
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise