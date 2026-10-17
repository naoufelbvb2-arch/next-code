"""Kernel configuration and environment settings."""

import os
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field


DEFAULT_BLACKLIST_PATTERNS: list[str] = [
    ".git",
    ".git/**",
    ".agent_os",
    ".agent_os/**",
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "__pycache__/**",
]


class KernelConfig(BaseModel):
    """Runtime configuration for Layer 1 Deterministic Kernel."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace_root: Path = Field(default_factory=lambda: Path.cwd().resolve())
    blacklist_patterns: list[str] = Field(
        default_factory=lambda: list(DEFAULT_BLACKLIST_PATTERNS)
    )
    default_command_timeout_sec: int = Field(default=30, ge=1, le=300)
    deepseek_api_key: str | None = Field(
        default_factory=lambda: os.getenv("DEEPSEEK_API_KEY")
    )
    deepseek_base_url: str = Field(
        default_factory=lambda: os.getenv(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"
        )
    )
    max_retries: int = Field(default=2, ge=0, le=10)
    max_tokens_per_task: int = Field(default=100_000, ge=1_000)
