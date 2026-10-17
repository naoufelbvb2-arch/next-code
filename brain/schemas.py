"""Core Pydantic v2 data contracts for Layer 2 (L2) - Live Codebase Engine & Memory."""

from enum import Enum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


# ==============================================================================
# 3.1 Structural World Model Contracts (RepoGraph)
# ==============================================================================
class SymbolKind(str, Enum):
    """Classification of code symbols extracted by AST."""

    CLASS = "CLASS"
    FUNCTION = "FUNCTION"
    ASYNC_FUNCTION = "ASYNC_FUNCTION"
    METHOD = "METHOD"
    GLOBAL_VAR = "GLOBAL_VAR"


class SymbolNode(BaseModel):
    """Structured code symbol definition."""

    model_config = ConfigDict(extra="forbid")

    name: str
    qualified_name: str
    kind: SymbolKind
    start_line: int
    end_line: int
    signature: str
    docstring_summary: str | None = None


class FileNode(BaseModel):
    """File-level structural node in the codebase graph."""

    model_config = ConfigDict(extra="forbid")

    rel_path: str
    language: str
    line_count: int
    sha256: str
    mtime_ns: int
    imports: list[str] = Field(default_factory=list)
    symbols: list[SymbolNode] = Field(default_factory=list)


class ImpactReport(BaseModel):
    """Blast radius and dependency impact analysis result."""

    model_config = ConfigDict(extra="forbid")

    target_file: str
    direct_dependents: list[str]
    transitive_dependents: list[str]
    dependencies: list[str]


# ==============================================================================
# 3.2 Working Memory & Blackboard Contracts (Blackboard)
# ==============================================================================
class TaskPhase(str, Enum):
    """High-level cognitive execution phases."""

    INITIALIZING = "INITIALIZING"
    EXPLORING = "EXPLORING"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class StepStatus(str, Enum):
    """Status lifecycle of an individual plan step."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class VerifiedFact(BaseModel):
    """Evidence-backed discovery recorded by exploration subagents."""

    model_config = ConfigDict(extra="forbid")

    fact_id: str
    statement: str
    evidence_file: str
    start_line: int
    end_line: int
    discovered_by: str = "SCOUT"


class PlanStep(BaseModel):
    """Atomic tactical plan step assigned to an operational role."""

    model_config = ConfigDict(extra="forbid")

    step_id: str
    description: str
    assigned_role: Literal["SCOUT", "SURGEON", "INQUISITOR"]
    target_files: list[str] = Field(default_factory=list)
    status: StepStatus = StepStatus.PENDING
    result_summary: str | None = None


class FailedHypothesis(BaseModel):
    """Anti-amnesia record of a failed execution attempt and reason for failure."""

    model_config = ConfigDict(extra="forbid")

    attempt_number: int
    hypothesis: str
    modified_files: list[str]
    failure_reason: str
    rolled_back_to_checkpoint: str | None = None
    timestamp_iso: str


class BlackboardState(BaseModel):
    """Complete persistent state envelope for a task execution."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    task_goal: str
    phase: TaskPhase = TaskPhase.INITIALIZING
    active_checkpoint_hash: str | None = None
    checkpoint_history: list[str] = Field(default_factory=list)
    verified_facts: list[VerifiedFact] = Field(default_factory=list)
    plan_steps: list[PlanStep] = Field(default_factory=list)
    failed_hypotheses: list[FailedHypothesis] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# ==============================================================================
# 3.3 Procedural Skill Contracts (Playbooks)
# ==============================================================================
class Playbook(BaseModel):
    """Procedural engineering standard operating procedure (SOP)."""

    model_config = ConfigDict(extra="forbid")

    playbook_id: str
    title: str
    trigger_keywords: list[str]
    applicable_phases: list[TaskPhase]
    sop_steps: list[str]
    verification_rules: list[str]
    anti_patterns: list[str]
