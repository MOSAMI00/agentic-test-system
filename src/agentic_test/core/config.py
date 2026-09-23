"""
System configuration management using Pydantic Settings.
Stage 1 Section 4.2.3 & Stage 3 Section 4.4.1.
"""

from typing import Optional
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Validated application settings loaded from environment variables and .env files.
    """
    model_config = SettingsConfigDict(
        env_prefix="AGENTIC_TEST_",
        env_file=".env",
        extra="ignore"
    )

    app_name: str = "Agentic Test Generation and Maintenance System"
    default_timeout_sec: float = 30.0
    memory_limit: str = "512m"
    cpu_quota: float = 1.0
    pids_limit: int = 100
    openai_api_key: Optional[SecretStr] = None


settings = Settings()
