"""
RecoveryAI — Database Migration Runner
Runs Alembic migrations programmatically (used by /api/seed endpoint).
Falls back to SQLAlchemy create_all when Alembic env is not configured.
"""

import asyncio
import logging
from pathlib import Path

from sqlalchemy import text

from app.database import Base, engine
from app.models import Customer, Invoice, Merchant, RecoveryEvent  # noqa: F401

logger = logging.getLogger(__name__)


async def run_migrations() -> None:
    """
    Create all tables defined in the ORM metadata for Supabase PostgreSQL.
    Uses `CREATE TABLE IF NOT EXISTS` semantics — safe to run multiple times.
    Also ensures the uuid-ossp / pgcrypto extensions are enabled.
    Explicitly adds new columns with ALTER TABLE IF NOT EXISTS so existing
    Supabase tables are updated without needing to drop/recreate anything.
    """
    async with engine.begin() as conn:
        # Enable UUID & cryptographic extensions on Supabase PostgreSQL
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";'))
        await conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto";'))
        # Create all ORM-mapped tables (IF NOT EXISTS)
        await conn.run_sync(Base.metadata.create_all)

        # ── Safe column additions for existing tables ──────────────────────────
        # These are idempotent — safe to run on every startup.
        await conn.execute(text(
            "ALTER TABLE invoices "
            "ADD COLUMN IF NOT EXISTS next_action_due_at TIMESTAMPTZ;"
        ))
        await conn.execute(text(
            "ALTER TABLE invoices "
            "ADD COLUMN IF NOT EXISTS call_pending BOOLEAN NOT NULL DEFAULT FALSE;"
        ))

    logger.info("✅  Migrations complete — all tables and columns ensured on Supabase PostgreSQL.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_migrations())
