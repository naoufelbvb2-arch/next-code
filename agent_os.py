import re
import uuid
from pathlib import Path

from architect.contracts import ConversationContext, TaskOutcome
from architect.lead_loop import LeadArchitectLoop
from brain.repo_graph import RepoGraph
from kernel.execution import (CancellationControl, ExecutionObserver, ExecutionBinding,
                              TaskCancelled, binding, check_cancelled)
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
        *,
        conversation_context: ConversationContext | None = None,
        observer: ExecutionObserver | None = None,
        cancellation: CancellationControl | None = None,
    ) -> TaskOutcome:
        """Run an autonomous engineering task from diagnosis to verified completion."""
        if conversation_context is not None:
            context_snapshot = ConversationContext.snapshot_and_validate(conversation_context)

            if context_snapshot.workspace_root.resolve() != self.workspace_root.resolve():
                raise ValueError(
                    f"ConversationContext workspace_root ({context_snapshot.workspace_root.resolve()}) "
                    f"does not match AgentOS workspace_root ({self.workspace_root.resolve()})."
                )
            if task_id is not None:
                if (
                    not isinstance(task_id, str)
                    or not (1 <= len(task_id) <= 64)
                    or not re.fullmatch(r"[a-zA-Z0-9_\-]+", task_id, flags=re.ASCII)
                ):
                    raise ValueError(
                        f"Invalid task_id format: '{task_id}'. Must be 1-64 ASCII alphanumeric, hyphen, or underscore characters without path separators or trailing newlines."
                    )
                if task_id == context_snapshot.conversation_id:
                    raise ValueError(
                        f"task_id '{task_id}' must be distinct from conversation_id '{context_snapshot.conversation_id}'."
                    )
                for prior in context_snapshot.prior_tasks:
                    if task_id == prior.task_id:
                        raise ValueError(
                            f"task_id '{task_id}' collides with prior task_id '{prior.task_id}'."
                        )
                state_file = self.workspace_root / ".agent_os" / "state" / f"{task_id}.json"
                if state_file.exists():
                    raise ValueError(
                        f"Pre-existing state file already exists for task_id '{task_id}': {state_file}"
                    )
                tid = task_id
            else:
                forbidden_ids = {context_snapshot.conversation_id}
                forbidden_ids.update(t.task_id for t in context_snapshot.prior_tasks)
                while True:
                    candidate = f"task_{uuid.uuid4().hex[:8]}"
                    candidate_file = self.workspace_root / ".agent_os" / "state" / f"{candidate}.json"
                    if candidate not in forbidden_ids and not candidate_file.exists():
                        tid = candidate
                        break
        else:
            context_snapshot = None
            tid = task_id or f"task_{uuid.uuid4().hex[:8]}"

        token = binding.set(ExecutionBinding(cancellation, observer))
        try:
            check_cancelled()
            loop = LeadArchitectLoop(
                workspace_root=self.workspace_root,
                gateway=self.gateway,
                dispatcher=self.dispatcher,
                task_id=tid,
                task_goal=task_goal,
                max_ticks=self.max_ticks,
                max_rollbacks=self.max_rollbacks,
                verbose=self.verbose,
                conversation_context=context_snapshot,
            )
            outcome = await loop.run()
            if observer is not None:
                observer.outcome(outcome)
            return outcome
        except TaskCancelled:
            if observer is not None:
                observer.cancelled(tid, self.workspace_root)
            raise
        except BaseException as error:
            if observer is not None:
                observer.failed(tid, self.workspace_root, error)
            raise
        finally:
            binding.reset(token)
