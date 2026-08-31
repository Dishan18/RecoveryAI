"""
RecoveryAI — Database Configuration
Async SQLAlchemy engine wired to Supabase PostgreSQL via asyncpg.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.config import settings

import uuid

# ── Engine (Pure Supabase PostgreSQL via asyncpg) ──────────────────────────────
engine = create_async_engine(
    settings.SUPABASE_DB_URL,
    echo=False,          # Set True for SQL query logging during development
    pool_pre_ping=True,  # Reconnect if connection drops
    poolclass=NullPool,  # Best practice for Supabase PgBouncer & Transaction Pooler
    connect_args={
        "statement_cache_size": 0,
        "prepared_statement_name_func": lambda: f"__stmt_{uuid.uuid4().hex}__",
    },
)

# ── Session factory ───────────────────────────────────────────────────────────
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

# ── Declarative base ──────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


# ── Dependency for FastAPI routes ─────────────────────────────────────────────
async def get_db() -> AsyncSession:
    """Yield a database session, ensuring it is closed after each request."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
