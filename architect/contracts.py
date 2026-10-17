"""Core data contracts for Layer 3 (L3) - The Lead Architect."""

from enum import Enum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

from brain.schemas import PlanStep, TaskPhase


# ==============================================================================
# 3.1 Worker Dispatch & Report Contracts (L3 <-> L4 Bridge)
# ==============================================================================
class WorkerStatus(str, Enum):
    """Execution status returned by an ephemeral worker."""

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class InquisitorVerdict(str, Enum):
    """Quality and verification verdict issued by an Inquisitor worker."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class VerifiedFactInput(BaseModel):
    """Fact proposal submitted by an ephemeral worker or Lead Architect."""

    model_config = ConfigDict(extra="forbid")

    statement: str
    evidence_file: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class TaskContract(BaseModel):
    """Isolated, role-scoped contract dispatched to an ephemeral worker."""

    model_config = ConfigDict(extra="forbid")

    contract_id: str
    target_role: Literal["SCOUT", "SURGEON", "INQUISITOR"]
    objective: str
    target_files: list[str] = Field(default_factory=list)
    context_briefing: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    test_command: str | None = None


class WorkerReport(BaseModel):
    """Structured report returned by an ephemeral worker after execution."""

    model_config = ConfigDict(extra="forbid")

    contract_id: str
    worker_role: Literal["SCOUT", "SURGEON", "INQUISITOR"]
    status: WorkerStatus
    summary: str
    discovered_facts: list[VerifiedFactInput] = Field(default_factory=list)
    modified_files: list[str] = Field(default_factory=list)
    inquisitor_verdict: InquisitorVerdict = InquisitorVerdict.NOT_APPLICABLE
    error_details: str | None = None


# ==============================================================================
# 3.2 Architect Cognitive & Decision Contracts
# ==============================================================================
class ArchitectAction(str, Enum):
    """Discrete operational decision made by the Lead Architect on each tick."""

    DISPATCH_SCOUT = "DISPATCH_SCOUT"
    DISPATCH_SURGEON = "DISPATCH_SURGEON"
    DISPATCH_INQUISITOR = "DISPATCH_INQUISITOR"
    COMPLETE_TASK = "COMPLETE_TASK"
    ABORT_TASK = "ABORT_TASK"


class MentalSimulation(BaseModel):
    """Explicit risk analysis and prediction required before taking action."""

    model_config = ConfigDict(extra="forbid")

    hypothesis: str
    predicted_side_effects: list[str] = Field(default_factory=list)
    verification_strategy: str


class ArchitectDecision(BaseModel):
    """Structured decision output produced by the Lead Architect LLM on each tick."""

    model_config = ConfigDict(extra="forbid")

    situation_assessment: str
    mental_simulation: MentalSimulation
    new_phase: TaskPhase
    updated_plan_steps: list[PlanStep] = Field(default_factory=list)
    new_facts_to_record: list[VerifiedFactInput] = Field(default_factory=list)
    next_action: ArchitectAction
    task_contract: TaskContract | None = None
    final_response: str | None = None


class TaskOutcome(BaseModel):
    """Final deterministic outcome returned by LeadArchitectLoop upon termination."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    success: bool
    final_phase: TaskPhase
    summary: str
    total_ticks: int
    rollbacks_triggered: int
    final_checkpoint_hash: str | None = None
