"""Core Pydantic v2 data contracts for Layer 1 Deterministic Kernel."""

from enum import Enum
from pathlib import Path
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class AgentRole(str, Enum):
    """Execution roles with deterministic RBAC boundaries."""

    ARCHITECT = "ARCHITECT"
    SCOUT = "SCOUT"
    SURGEON = "SURGEON"
    INQUISITOR = "INQUISITOR"


class ExecutionContext(BaseModel):
    """Contextual envelope required for every kernel and tool execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str
    role: AgentRole
    workspace_root: Path
    allowed_files: frozenset[str] = Field(default_factory=frozenset)
    command_timeout_sec: int = Field(default=30, ge=1, le=300)


class ToolResult(BaseModel):
    """Deterministic return contract for all tool invocations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    success: bool
    output: str
    error_code: str | None = None
    execution_time_ms: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class PatchBlock(BaseModel):
    """Atomic block replacement contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    search: str
    replace: str


class ModelProfile(str, Enum):
    """Model routing profiles."""

    REASONING = "REASONING"
    FAST_WORKER = "FAST_WORKER"
    LOCAL_CODER = "LOCAL_CODER"


class Message(BaseModel):
    """Normalized chat message for LLM gateway."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    name: str | None = None
    tool_call_id: str | None = None


class TokenUsage(BaseModel):
    """Token consumption and latency tracking contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: float
    model_name: str
    task_id: str
