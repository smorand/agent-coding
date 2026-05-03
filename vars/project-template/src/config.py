"""Application settings using pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables.

    Environment variables are prefixed with __PROJECT_PREFIX_UPPER__
    (e.g., __PROJECT_PREFIX_UPPER__APP_NAME). A .env file is loaded
    automatically if present.
    """

    model_config = SettingsConfigDict(
        env_prefix="__PROJECT_PREFIX_UPPER__",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    app_name: str = "__PROJECT_NAME__"
    debug: bool = False
