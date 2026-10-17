"""Layer 4 (L4) - Standard Agent Template & Ephemeral Squads (squad)."""

from squad.chassis import StandardAgentChassis
from squad.dispatcher import EphemeralSquadDispatcher
from squad.memory import EphemeralWorkingMemory
from squad.role_cards import (
    INQUISITOR_ROLE_CARD,
    SCOUT_ROLE_CARD,
    SURGEON_ROLE_CARD,
    get_role_card,
)
from squad.schemas import (
    ChassisStepDecision,
    ContextPackage,
    FinalReportDraft,
    InteractionRecord,
    RoleCard,
    ToolCallSpec,
)

__all__ = [
    "RoleCard",
    "ContextPackage",
    "ToolCallSpec",
    "FinalReportDraft",
    "ChassisStepDecision",
    "InteractionRecord",
    "SCOUT_ROLE_CARD",
    "SURGEON_ROLE_CARD",
    "INQUISITOR_ROLE_CARD",
    "get_role_card",
    "EphemeralWorkingMemory",
    "StandardAgentChassis",
    "EphemeralSquadDispatcher",
]
