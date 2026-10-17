"""Core data contracts for Layer 4 (L4) - Standard Agent Template & Ephemeral Squads."""

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

from architect.contracts import (
    InquisitorVerdict,
    TaskContract,
    VerifiedFactInput,
    WorkerStatus,
)
from kernel.schemas import ModelProfile


# ==============================================================================
# 3.1 Role Card & Context Package Contracts
# ==============================================================================
class RoleCard(BaseModel):
    """Runtime configuration defining agent persona, security whitelist, and operational limits."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    role_id: Literal["SCOUT", "SURGEON", "INQUISITOR"]
    persona_title: str
    system_directive: str
    tools_whitelist: tuple[str, ...]
    model_profile: ModelProfile = ModelProfile.FAST_WORKER
    max_steps: int = Field(default=10, ge=1, le=30)
    sliding_window_size: int = Field(default=4, ge=1, le=15)
    require_test_execution_if_configured: bool = False


class ContextPackage(BaseModel):
    """Surgical context bundle delivered to an ephemeral agent upon instantiation."""

    model_config = ConfigDict(extra="forbid")

    contract: TaskContract
    target_file_skeletons: dict[str, str] = Field(default_factory=dict)
    git_diff: str = ""
    custom_constraints: list[str] = Field(default_factory=list)


# ==============================================================================
# 3.2 Chassis Step & Tool Call Contracts
# ==============================================================================
class ToolCallSpec(BaseModel):
    """Invocation request for a sandboxed Layer 1 tool."""

    model_config = ConfigDict(extra="forbid")

    tool_name: Literal[
        "read_file",
        "list_dir",
        "apply_patch",
        "grep_search",
        "find_files",
        "run_command",
    ]
    arguments: dict[str, Any] = Field(default_factory=dict)


class FinalReportDraft(BaseModel):
    """Draft report proposed by worker upon task completion before chassis sanitization."""

    model_config = ConfigDict(extra="forbid")

    status: WorkerStatus
    summary: str
    discovered_facts: list[VerifiedFactInput] = Field(default_factory=list)
    inquisitor_verdict: InquisitorVerdict = InquisitorVerdict.NOT_APPLICABLE
    error_details: str | None = None


class ChassisStepDecision(BaseModel):
    """Single-step ReAct decision emitted by the LLM on each chassis execution step."""

    model_config = ConfigDict(extra="forbid")

    thought: str
    scratchpad_note: str | None = None
    action_type: Literal["CALL_TOOL", "FINISH_TASK"]
    tool_call: ToolCallSpec | None = None
    final_report: FinalReportDraft | None = None


# ==============================================================================
# 3.3 Ephemeral Memory Contracts
# ==============================================================================
class InteractionRecord(BaseModel):
    """Record of a single tool interaction executed within the ephemeral working memory."""

    model_config = ConfigDict(extra="forbid")

    step_number: int
    thought: str
    tool_name: str
    tool_args_summary: str
    tool_success: bool
    raw_output: str
    compressed_summary: str
