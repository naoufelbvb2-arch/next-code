"""Top-level AgentOS facade unifying Layer 1, Layer 2, Layer 3, and Layer 4."""

import uuid
from pathlib import Path

from architect.contracts import TaskOutcome
from architect.lead_loop import LeadArchitectLoop
from brain.repo_graph import RepoGraph
from kernel.config import KernelConfig
from kernel.gateway.router import ModelGateway
from kernel.tools.registry import ToolRegistry
from squad.dispatcher import EphemeralSquadDispatcher


class AgentOS:
    """Unified operating system facade for autonomous engineering workflows."""

    def __init__(
        self,
        workspace_root: Path,
        gateway: ModelGateway | None = None,
        max_ticks: int = 15,
        max_rollbacks: int = 4,
        verbose: bool = False,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.config = KernelConfig(workspace_root=self.workspace_root)
        self.gateway = gateway or ModelGateway(config=self.config)
        self.max_ticks = max_ticks
        self.max_rollbacks = max_rollbacks
        self.verbose = verbose

        self.repo_graph = RepoGraph(self.workspace_root)
        self.tool_registry = ToolRegistry()
        self.dispatcher = EphemeralSquadDispatcher(
            workspace_root=self.workspace_root,
            gateway=self.gateway,
            repo_graph=self.repo_graph,
            tool_registry=self.tool_registry,
        )

    async def execute_task(
        self,
        task_goal: str,
        task_id: str | None = None,
    ) -> TaskOutcome:
        """Run an autonomous engineering task from diagnosis to verified completion."""
        tid = task_id or f"task_{uuid.uuid4().hex[:8]}"
        loop = LeadArchitectLoop(
            workspace_root=self.workspace_root,
            gateway=self.gateway,
            dispatcher=self.dispatcher,
            task_id=tid,
            task_goal=task_goal,
            max_ticks=self.max_ticks,
            max_rollbacks=self.max_rollbacks,
            verbose=self.verbose,
        )
        return await loop.run()
