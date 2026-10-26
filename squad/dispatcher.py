"""EphemeralSquadDispatcher implementing L3 WorkerDispatcher protocol."""

from pathlib import Path
from uuid import uuid4

from architect.contracts import TaskContract, WorkerReport
from brain.repo_graph import RepoGraph
from kernel.execution import check_cancelled, observe_optional, CleanupUnverified
from kernel.gateway.router import ModelGateway
from kernel.schemas import ExecutionContext
from kernel.tools.registry import ToolRegistry
from squad.chassis import StandardAgentChassis
from squad.role_cards import get_role_card
from squad.schemas import ContextPackage


class EphemeralSquadDispatcher:
    """Dispatches TaskContracts to dynamically parameterized, ephemeral StandardAgentChassis workers."""

    def __init__(
        self,
        workspace_root: Path,
        gateway: ModelGateway,
        repo_graph: RepoGraph | None = None,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self.workspace_root = workspace_root
        self.gateway = gateway
        self.repo_graph = repo_graph or RepoGraph(workspace_root)
        self.tool_registry = tool_registry or ToolRegistry()

    async def dispatch(
        self,
        contract: TaskContract,
        ctx: ExecutionContext,
        git_diff: str = "",
    ) -> WorkerReport:
        """Spawn an ephemeral worker, execute the contract, verify memory wipe, and return the report."""
        check_cancelled()
        # 1. Refresh repository AST graph and compile target skeletons
        self.repo_graph.refresh()
        target_file_skeletons: dict[str, str] = {}
        for f in contract.target_files:
            target_file_skeletons[f] = self.repo_graph.get_file_skeleton(f)

        # 2. Select configured RoleCard
        role_card = get_role_card(contract.target_role)

        if ctx.role.value != contract.target_role:
            raise ValueError("Worker role does not match its execution context.")

        # 3. Assemble surgical ContextPackage
        context_pkg = ContextPackage(
            contract=contract,
            target_file_skeletons=target_file_skeletons,
            git_diff=git_diff,
        )

        # 4. Spawn clean ephemeral chassis
        check_cancelled()
        chassis = StandardAgentChassis(
            role_card=role_card,
            context_pkg=context_pkg,
            ctx=ctx,
            gateway=self.gateway,
            tool_registry=self.tool_registry,
        )

        # Identity belongs to this actual invocation, never to a model contract.
        worker_ref = uuid4().hex
        check_cancelled()
        observe_optional("worker_started", ctx.task_id, worker_ref, ctx.role)
        check_cancelled()
        # 5. Await execution (including chassis finally/memory destruction).
        report = await chassis.execute()

        # 6. Ephemeral destruction invariant verification
        assert chassis.is_alive is False, "Ephemeral chassis failed to terminate cleanly!"
        assert (
            len(chassis.memory.history) == 0
        ), "Ephemeral memory was not wiped on termination!"

        if chassis.is_alive or chassis.memory.history:
            raise CleanupUnverified("Worker destruction could not be established.")
        if not isinstance(report, WorkerReport) or report.worker_role != ctx.role.value or report.contract_id != contract.contract_id:
            raise ValueError("Worker report does not match the actual invocation.")
        check_cancelled()
        observe_optional("worker_completed", ctx.task_id, worker_ref, report)
        check_cancelled()
        return report
