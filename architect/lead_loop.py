"""LeadArchitectLoop state machine and deterministic safety gate orchestration engine."""

import re
from pathlib import Path

from architect.contracts import (
    ArchitectAction,
    ArchitectDecision,
    ConversationContext,
    InquisitorVerdict,
    TaskContract,
    TaskOutcome,
    WorkerReport,
    WorkerStatus,
)
from architect.dispatcher import WorkerDispatcher
from architect.prompts import (
    build_architect_system_prompt,
    build_architect_tick_prompt,
)
from brain.blackboard import Blackboard
from brain.playbooks import PlaybookStore
from brain.repo_graph import RepoGraph
from brain.schemas import StepStatus, TaskPhase
from kernel.checkpoint import GitShadowCheckpointer
from kernel.gateway.router import ModelGateway
from kernel.schemas import AgentRole, ExecutionContext, Message, ModelProfile


class LeadArchitectLoop:
    """The Lead Architect orchestration engine enforcing deterministic safety gates."""

    def __init__(
        self,
        workspace_root: Path,
        gateway: ModelGateway,
        dispatcher: WorkerDispatcher,
        task_id: str,
        task_goal: str,
        max_ticks: int = 15,
        max_rollbacks: int = 4,
        verbose: bool = False,
        conversation_context: ConversationContext | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.gateway = gateway
        self.dispatcher = dispatcher
        self.task_id = task_id
        self.task_goal = task_goal
        self.max_ticks = max_ticks
        self.max_rollbacks = max_rollbacks
        self.verbose = verbose
        if conversation_context is not None:
            if (
                len(conversation_context.prior_tasks) > 3
                or len(conversation_context.recent_messages) > 6
                or len(conversation_context.advisory_preferences) > 5
            ):
                raise ValueError("ConversationContext collection caps exceeded at trust boundary.")
            self.conversation_context = conversation_context.model_copy(deep=True)

            if self.conversation_context.workspace_root.resolve() != self.workspace_root.resolve():
                raise ValueError(
                    f"ConversationContext workspace_root ({self.conversation_context.workspace_root.resolve()}) "
                    f"does not match LeadArchitectLoop workspace_root ({self.workspace_root.resolve()})."
                )
            if (
                not isinstance(self.task_id, str)
                or not (1 <= len(self.task_id) <= 64)
                or not re.fullmatch(r"[a-zA-Z0-9_\-]+", self.task_id, flags=re.ASCII)
            ):
                raise ValueError(
                    f"Invalid task_id format: '{self.task_id}'. Must be 1-64 ASCII alphanumeric, hyphen, or underscore characters without path separators or trailing newlines."
                )
            if self.task_id == self.conversation_context.conversation_id:
                raise ValueError(
                    f"New task_id '{self.task_id}' must be distinct from conversation_id '{self.conversation_context.conversation_id}'."
                )
            for prior in self.conversation_context.prior_tasks:
                if self.task_id == prior.task_id:
                    raise ValueError(
                        f"New task_id '{self.task_id}' collides with prior task_id '{prior.task_id}'."
                    )
            state_file = self.workspace_root / ".agent_os" / "state" / f"{self.task_id}.json"
            if state_file.exists():
                raise ValueError(
                    f"Pre-existing state file already exists for task_id '{self.task_id}': {state_file}"
                )
        else:
            self.conversation_context = None

        # Layer 2 Components
        self.repo_graph = RepoGraph(self.workspace_root)
        self.blackboard = Blackboard(
            self.workspace_root, task_id=self.task_id, task_goal=self.task_goal
        )
        self.playbooks = PlaybookStore(self.workspace_root)

        # Layer 1 Shadow Checkpointer
        self.checkpointer = GitShadowCheckpointer(self.workspace_root)

        # Safety Gate State Tracking
        self.unverified_mutation_pending: bool = False
        self.last_pre_surgery_checkpoint: str | None = None
        self.last_surgery_hypothesis: str = ""
        self.last_surgery_files: list[str] = []
        self.rollbacks_triggered: int = 0
        self.last_worker_report: WorkerReport | None = None
        self.gate_warning: str | None = None

    def _execute_rollback_and_graveyard(self, reason: str) -> None:
        """Deterministically roll back workspace, record failed hypothesis, and update memory."""
        if self.last_pre_surgery_checkpoint:
            self.checkpointer.rollback(self.last_pre_surgery_checkpoint)

        self.blackboard.record_failed_hypothesis(
            hypothesis=self.last_surgery_hypothesis or "Unspecified hypothesis",
            modified_files=self.last_surgery_files,
            failure_reason=reason,
            rolled_back_to_checkpoint=self.last_pre_surgery_checkpoint,
        )

        self.unverified_mutation_pending = False
        self.rollbacks_triggered += 1
        self.repo_graph.refresh()
        if self.verbose:
            cp_short = self.last_pre_surgery_checkpoint[:8] if self.last_pre_surgery_checkpoint else "initial"
            print(f"  [Gate 3 Auto-Rollback] Workspace restored to {cp_short}. Reason: {reason}")

    async def run(self) -> TaskOutcome:
        """Run the Lead Architect orchestration cycle until task completion, abortion, or limits."""
        # 1. Initial world model refresh and baseline checkpoint
        self.repo_graph.refresh()
        base_checkpoint = self.checkpointer.create_checkpoint("task_baseline")
        self.blackboard.record_checkpoint(base_checkpoint)

        # 2. Match playbooks based on task goal
        matched_pbs = self.playbooks.match_playbooks(self.task_goal)
        playbooks_prompt = self.playbooks.render_playbooks_prompt(matched_pbs)

        # 3. Main orchestration tick loop
        for tick in range(1, self.max_ticks + 1):
            # Abort if rollbacks exceed budget
            if self.rollbacks_triggered >= self.max_rollbacks:
                self.blackboard.set_phase(TaskPhase.FAILED)
                return TaskOutcome(
                    task_id=self.task_id,
                    success=False,
                    final_phase=TaskPhase.FAILED,
                    summary=f"Exceeded maximum allowed rollbacks ({self.max_rollbacks}).",
                    total_ticks=tick,
                    rollbacks_triggered=self.rollbacks_triggered,
                    final_checkpoint_hash=self.blackboard.state.active_checkpoint_hash,
                )

            # Assemble active target files for focused repo mapping and blast-radius analysis
            active_target_files: list[str] = []
            for step in self.blackboard.state.plan_steps:
                if step.status in (StepStatus.IN_PROGRESS, StepStatus.PENDING):
                    active_target_files.extend(step.target_files)
            active_target_files = list(
                dict.fromkeys(active_target_files + self.last_surgery_files)
            )

            # Compile context
            context_digest = self.blackboard.compile_context_digest(max_chars=6000)
            repo_map = self.repo_graph.render_repo_map(
                max_chars=6000, focus_files=active_target_files
            )
            impact_reports = [
                self.repo_graph.get_impact_radius(f)
                for f in active_target_files
                if f in self.repo_graph.graph
            ][:5]

            tick_prompt = build_architect_tick_prompt(
                playbooks_prompt=playbooks_prompt,
                context_digest=context_digest,
                repo_map=repo_map,
                impact_reports=impact_reports,
                last_worker_report=self.last_worker_report,
                gate_warning=self.gate_warning,
                tick_number=tick,
                max_ticks=self.max_ticks,
                conversation_context=self.conversation_context,
            )

            # Clear gate warning after presenting it
            self.gate_warning = None

            # Generate structured Architect decision
            messages = [
                Message(role="system", content=build_architect_system_prompt()),
                Message(role="user", content=tick_prompt),
            ]

            decision, _ = await self.gateway.generate_structured(
                profile=ModelProfile.REASONING,
                messages=messages,
                response_model=ArchitectDecision,
                task_id=self.task_id,
            )

            if self.verbose:
                print(f"\n[Architect Tick {tick}/{self.max_ticks}] Phase: {decision.new_phase.value} | Action: {decision.next_action.value}")
                print(f"  Assessment: {decision.situation_assessment}")
                print(f"  Hypothesis: {decision.mental_simulation.hypothesis}")
                if decision.task_contract:
                    print(f"  Contract: [{decision.task_contract.target_role}] {decision.task_contract.objective}")

            # Apply updates to Blackboard
            self.blackboard.set_phase(decision.new_phase)
            if decision.updated_plan_steps:
                self.blackboard.set_plan(decision.updated_plan_steps)
            for fact_in in decision.new_facts_to_record:
                self.blackboard.add_fact(
                    statement=fact_in.statement,
                    evidence_file=fact_in.evidence_file,
                    start_line=fact_in.start_line,
                    end_line=fact_in.end_line,
                    discovered_by="ARCHITECT",
                )

            # Gate 4: Intercept premature COMPLETE_TASK if unverified mutations are pending
            if (
                decision.next_action == ArchitectAction.COMPLETE_TASK
                and self.unverified_mutation_pending
            ):
                decision.next_action = ArchitectAction.DISPATCH_INQUISITOR
                target_files = list(self.last_surgery_files)
                decision.task_contract = TaskContract(
                    contract_id=f"auto_inquisitor_tick_{tick}",
                    target_role="INQUISITOR",
                    objective="Inspect git diff and verify acceptance criteria before task completion.",
                    target_files=target_files,
                    context_briefing="Unverified code mutations are pending. Full Inquisitor approval is required.",
                    acceptance_criteria=[
                        "All acceptance criteria met",
                        "Zero unintended regressions",
                    ],
                )
                self.gate_warning = (
                    "Gate 4 Overridden: Attempted COMPLETE_TASK while unverified mutations were pending. "
                    "Mandatory DISPATCH_INQUISITOR scheduled."
                )

            # Completion / Abort execution
            if decision.next_action == ArchitectAction.COMPLETE_TASK:
                if self.verbose:
                    print(f"\n[Architect Complete] {decision.final_response or 'Task completed successfully.'}")
                self.blackboard.set_phase(TaskPhase.COMPLETED)
                return TaskOutcome(
                    task_id=self.task_id,
                    success=True,
                    final_phase=TaskPhase.COMPLETED,
                    summary=decision.final_response or "Task completed successfully.",
                    total_ticks=tick,
                    rollbacks_triggered=self.rollbacks_triggered,
                    final_checkpoint_hash=self.blackboard.state.active_checkpoint_hash,
                )

            if decision.next_action == ArchitectAction.ABORT_TASK:
                self.blackboard.set_phase(TaskPhase.FAILED)
                return TaskOutcome(
                    task_id=self.task_id,
                    success=False,
                    final_phase=TaskPhase.FAILED,
                    summary=decision.final_response or "Task aborted by Lead Architect.",
                    total_ticks=tick,
                    rollbacks_triggered=self.rollbacks_triggered,
                    final_checkpoint_hash=self.blackboard.state.active_checkpoint_hash,
                )

            # Gate 1: No Blind Surgery
            if decision.next_action == ArchitectAction.DISPATCH_SURGEON:
                if (
                    not decision.task_contract
                    or not decision.task_contract.target_files
                ):
                    self.gate_warning = (
                        "Gate 1 Interception: DISPATCH_SURGEON blocked because target_files was empty. "
                        "Surgery without explicit target files is strictly forbidden. Scout first."
                    )
                    self.blackboard.add_note(
                        f"Tick {tick}: Gate 1 Violation (No Blind Surgery). Blocked surgeon dispatch."
                    )
                    continue

            # Map action to worker role
            if decision.next_action == ArchitectAction.DISPATCH_SCOUT:
                role = AgentRole.SCOUT
            elif decision.next_action == ArchitectAction.DISPATCH_SURGEON:
                role = AgentRole.SURGEON
            elif decision.next_action == ArchitectAction.DISPATCH_INQUISITOR:
                role = AgentRole.INQUISITOR
            else:
                role = AgentRole.SCOUT

            # Gate 2: Mandatory Pre-Surgery Shadow Checkpoint
            if role == AgentRole.SURGEON:
                cp_hash = self.checkpointer.create_checkpoint(f"pre_surgery_tick_{tick}")
                self.blackboard.record_checkpoint(cp_hash)
                self.last_pre_surgery_checkpoint = cp_hash
                self.last_surgery_hypothesis = (
                    decision.mental_simulation.hypothesis
                )
                self.last_surgery_files = list(
                    decision.task_contract.target_files if decision.task_contract else []
                )

            # Build ExecutionContext
            allowed_files = (
                frozenset(decision.task_contract.target_files)
                if role == AgentRole.SURGEON and decision.task_contract
                else frozenset()
            )
            ctx = ExecutionContext(
                task_id=self.task_id,
                role=role,
                workspace_root=self.workspace_root,
                allowed_files=allowed_files,
            )

            # Compute diff since last pre-surgery checkpoint
            git_diff = ""
            if self.last_pre_surgery_checkpoint:
                try:
                    git_diff = self.checkpointer.get_diff(
                        self.last_pre_surgery_checkpoint
                    )
                except Exception:
                    git_diff = ""

            # Dispatch worker
            assert decision.task_contract is not None
            if self.verbose:
                print(f"  -> Dispatched ephemeral {role.value} worker...")
            report = await self.dispatcher.dispatch(
                contract=decision.task_contract,
                ctx=ctx,
                git_diff=git_diff,
            )
            self.last_worker_report = report
            if self.verbose:
                print(f"  <- [{report.worker_role}] {report.status.value}: {report.summary}")
                if report.modified_files:
                    print(f"     Mutations: {report.modified_files}")
                if report.discovered_facts:
                    print(f"     Discovered facts: {[f.statement for f in report.discovered_facts]}")

            # Record any facts discovered by the worker
            for df in report.discovered_facts:
                self.blackboard.add_fact(
                    statement=df.statement,
                    evidence_file=df.evidence_file,
                    start_line=df.start_line,
                    end_line=df.end_line,
                    discovered_by=report.worker_role,
                )

            # Refresh structural graph
            self.repo_graph.refresh()

            # Gate 3: Evaluate worker report & trigger auto-rollback + graveyard
            if report.worker_role == "SURGEON":
                if report.status == WorkerStatus.SUCCESS:
                    self.unverified_mutation_pending = True
                    if report.modified_files:
                        self.last_surgery_files = list(report.modified_files)
                else:
                    reason_parts = []
                    if report.summary:
                        reason_parts.append(report.summary)
                    if report.error_details and report.error_details != report.summary:
                        reason_parts.append(report.error_details)
                    reason_text = " - ".join(reason_parts) if reason_parts else "Surgeon Failure"
                    self._execute_rollback_and_graveyard(
                        reason=f"Surgeon Failed: {reason_text}"
                    )

            elif report.worker_role == "INQUISITOR":
                if (
                    report.inquisitor_verdict == InquisitorVerdict.REJECTED
                    or report.status == WorkerStatus.FAILED
                ):
                    reason_parts = []
                    if report.summary:
                        reason_parts.append(report.summary)
                    if report.error_details and report.error_details != report.summary:
                        reason_parts.append(report.error_details)
                    reason_text = " - ".join(reason_parts) if reason_parts else "Inquisitor Rejected"
                    self._execute_rollback_and_graveyard(
                        reason=f"Inquisitor Rejected: {reason_text}"
                    )
                elif (
                    report.inquisitor_verdict == InquisitorVerdict.APPROVED
                    and report.status == WorkerStatus.SUCCESS
                ):
                    self.unverified_mutation_pending = False

        # Max ticks exhausted
        self.blackboard.set_phase(TaskPhase.FAILED)
        return TaskOutcome(
            task_id=self.task_id,
            success=False,
            final_phase=TaskPhase.FAILED,
            summary=f"Exceeded maximum allowed ticks ({self.max_ticks}) without completion.",
            total_ticks=self.max_ticks,
            rollbacks_triggered=self.rollbacks_triggered,
            final_checkpoint_hash=self.blackboard.state.active_checkpoint_hash,
        )
