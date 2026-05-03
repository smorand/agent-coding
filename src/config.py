"""Application settings using pydantic-settings."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables.

    Environment variables are prefixed with AGENT_CODE_
    (e.g., AGENT_CODE_APP_NAME). A .env file is loaded
    automatically if present.
    """

    model_config = SettingsConfigDict(
        env_prefix="AGENT_CODE_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    app_name: str = "agent-code"
    debug: bool = False
