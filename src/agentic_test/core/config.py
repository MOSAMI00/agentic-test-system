"""
System configuration management using Pydantic Settings.
Stage 1 Section 4.2.3 & Stage 3 Section 4.4.1.

F-05: Lazy loading via get_settings() eliminates eager import-time .env loading.
Backward-compatible module-level `settings` attribute is preserved via a lazy
proxy so that `from agentic_test.core.config import settings` continues to work
without eager instantiation at import time.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Validated application settings loaded from environment variables and .env files.
    """

    model_config = SettingsConfigDict(
        env_prefix="AGENTIC_TEST_",
        env_file=".env",
        extra="ignore",
    )

    app_name: str = "Agentic Test Generation and Maintenance System"
    default_timeout_sec: float = 30.0
    memory_limit: str = "512m"
    cpu_quota: float = 1.0
    pids_limit: int = 100
    openai_api_key: SecretStr | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the singleton Settings instance, loading .env on first call only.
    Subsequent calls return the cached instance with zero I/O.
    """
    return Settings()


# ---------------------------------------------------------------------------
# Lazy backward-compatible module-level 'settings' attribute (F-05).
# Creating the lightweight proxy object at import time does NOT instantiate
# Settings() or read .env files; get_settings() is invoked only on first
# attribute access.
# ---------------------------------------------------------------------------


class _LazySettingsProxy:
    """
    Transparent proxy that forwards attribute lookups and mutations to get_settings().
    Uses __slots__ = () so proxy has no local instance __dict__, keeping
    settings.field = ... and pytest monkeypatch.setattr(settings, ...) in sync
    with get_settings().field without instantiating Settings() at import time.
    """

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        return getattr(get_settings(), name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(get_settings(), name, value)

    def __delattr__(self, name: str) -> None:
        delattr(get_settings(), name)

    def __repr__(self) -> str:
        return repr(get_settings())


settings: Settings = _LazySettingsProxy()  # type: ignore[assignment]
