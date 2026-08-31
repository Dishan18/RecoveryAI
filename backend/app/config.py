"""
RecoveryAI — Application Configuration
Reads settings from .env (or environment) using Pydantic Settings.

Strictly enforces Supabase PostgreSQL via asyncpg driver (`postgresql+asyncpg://`).
"""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("backend/.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database — strictly Supabase PostgreSQL connection string from .env
    SUPABASE_DB_URL: str = ""

    # External APIs
    GEMINI_API_KEY: str = ""
    SARVAM_API_KEY: str = ""

    # App meta
    APP_NAME: str = "RecoveryAI"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False

    @field_validator("SUPABASE_DB_URL", mode="before")
    @classmethod
    def normalise_db_driver(cls, v: str) -> str:
        """
        Validate and normalize Supabase PostgreSQL URL for SQLAlchemy + asyncpg.
        Auto-converts postgresql:// or postgres:// to postgresql+asyncpg://.
        """
        if not v or not str(v).strip():
            raise ValueError(
                "SUPABASE_DB_URL is missing. Please set your Supabase PostgreSQL connection string in backend/.env"
            )
        v_str = str(v).strip()
        if "YOUR_PASSWORD" in v_str or "YOUR_PROJECT_REF" in v_str or "password@db.supabase.co" in v_str:
            raise ValueError(
                "SUPABASE_DB_URL contains default placeholders. Please replace them with your actual Supabase credentials in backend/.env"
            )

        for bare in ("postgres://", "postgresql://"):
            if v_str.startswith(bare):
                return "postgresql+asyncpg://" + v_str[len(bare):]
        return v_str


settings = Settings()
