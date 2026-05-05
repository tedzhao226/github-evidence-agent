from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


TraceBackend = Literal["both", "local", "remote"]


class AppSettings(BaseSettings):
    """Runtime configuration parsed from environment and .env."""

    openai_api_key: str | None = None
    logfire_token: str | None = None
    trace_backend: TraceBackend = "both"
    logfire_console: bool = False
    github_token: str | None = None
    openai_model: str = "openai:gpt-5-mini"
    clone_root: Path = Path("tmp/repos")
    sandbox_root: Path = Path("tmp/sandbox/sessions")
    prompt_config: Path = Path("prompts/agent.yaml")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("openai_api_key", "logfire_token", "github_token", mode="before")
    @classmethod
    def empty_string_to_none(cls, value: Any) -> str | None:
        """Treat blank secrets as absent."""
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return str(value)

    @field_validator("trace_backend", mode="before")
    @classmethod
    def valid_trace_backend_or_default(cls, value: Any) -> TraceBackend:
        """Keep the old fallback behavior for invalid TRACE_BACKEND values."""
        if isinstance(value, str) and value.lower().strip() in {"both", "local", "remote"}:
            return cast(TraceBackend, value.lower().strip())
        return "both"


def load_settings(env_file: str | Path | None = ".env") -> AppSettings:
    """Load runtime configuration with shell environment taking precedence."""
    if env_file is None:
        return AppSettings(_env_file=None)
    return AppSettings(_env_file=env_file)
