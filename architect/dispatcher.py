"""WorkerDispatcher protocol and ScriptedMockDispatcher for test simulation."""

import inspect
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from architect.contracts import TaskContract, WorkerReport, WorkerStatus
from kernel.schemas import ExecutionContext

HandlerType = Callable[[TaskContract, ExecutionContext, str], Awaitable[WorkerReport] | WorkerReport]


@runtime_checkable
class WorkerDispatcher(Protocol):
    """Protocol for dispatching isolated TaskContracts to Layer 4 workers."""

    async def dispatch(
        self,
        contract: TaskContract,
        ctx: ExecutionContext,
        git_diff: str = "",
    ) -> WorkerReport:
        """Execute contract under the given execution context and return structured report."""
        ...


class ScriptedMockDispatcher:
    """Deterministic mock dispatcher for testing LeadArchitectLoop behaviors and safety gates."""

    def __init__(
        self,
        reports: list[WorkerReport] | None = None,
        handler: HandlerType | None = None,
    ) -> None:
        self._reports_queue: list[WorkerReport] = list(reports) if reports else []
        self._handler = handler
        self.dispatched_calls: list[dict[str, Any]] = []

    def add_report(self, report: WorkerReport) -> None:
        """Enqueue a scripted WorkerReport to be returned on the next dispatch."""
        self._reports_queue.append(report)

    async def dispatch(
        self,
        contract: TaskContract,
        ctx: ExecutionContext,
        git_diff: str = "",
    ) -> WorkerReport:
        """Record the call and return the next scripted report or execute custom handler."""
        self.dispatched_calls.append(
            {
                "contract": contract,
                "context": ctx,
                "git_diff": git_diff,
            }
        )

        if self._handler is not None:
            res = self._handler(contract, ctx, git_diff)
            if inspect.isawaitable(res):
                return await res
            return res

        if self._reports_queue:
            return self._reports_queue.pop(0)

        # Fallback default report if no scripted reports remain
        return WorkerReport(
            contract_id=contract.contract_id,
            worker_role=contract.target_role,
            status=WorkerStatus.SUCCESS,
            summary=f"Mock execution of {contract.contract_id} completed successfully.",
        )
