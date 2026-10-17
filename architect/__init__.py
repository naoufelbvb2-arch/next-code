"""Layer 3 (L3) - The Lead Architect (architect).

Executive reasoning, clean-room context orchestration, and deterministic safety gates.
"""

from architect.contracts import (
    ArchitectAction,
    ArchitectDecision,
    InquisitorVerdict,
    MentalSimulation,
    TaskContract,
    TaskOutcome,
    VerifiedFactInput,
    WorkerReport,
    WorkerStatus,
)
from architect.dispatcher import ScriptedMockDispatcher, WorkerDispatcher
from architect.lead_loop import LeadArchitectLoop
from architect.prompts import (
    build_architect_system_prompt,
    build_architect_tick_prompt,
)

__all__ = [
    "WorkerStatus",
    "InquisitorVerdict",
    "VerifiedFactInput",
    "TaskContract",
    "WorkerReport",
    "ArchitectAction",
    "MentalSimulation",
    "ArchitectDecision",
    "TaskOutcome",
    "WorkerDispatcher",
    "ScriptedMockDispatcher",
    "build_architect_system_prompt",
    "build_architect_tick_prompt",
    "LeadArchitectLoop",
]
