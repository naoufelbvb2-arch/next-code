"""Universal StandardAgentChassis implementing the immutable ReAct execution engine."""

from typing import Any

from architect.contracts import (
    InquisitorVerdict,
    WorkerReport,
    WorkerStatus,
)
from kernel.execution import check_cancelled, gateway_wait, tool_wait
from kernel.gateway.router import ModelGateway
from kernel.schemas import ExecutionContext, Message, PatchBlock, ToolResult
from kernel.tools.registry import ToolRegistry
from squad.memory import EphemeralWorkingMemory
from squad.schemas import (
    ChassisStepDecision,
    ContextPackage,
    FinalReportDraft,
    RoleCard,
    ToolCallSpec,
)


class StandardAgentChassis:
    """Universal execution chassis for all ephemeral workers (SCOUT, SURGEON, INQUISITOR)."""

    def __init__(
        self,
        role_card: RoleCard,
        context_pkg: ContextPackage,
        ctx: ExecutionContext,
        gateway: ModelGateway,
        tool_registry: ToolRegistry,
    ) -> None:
        self.role_card = role_card
        self.context_pkg = context_pkg
        self.ctx = ctx
        self.gateway = gateway
        self.tool_registry = tool_registry
        self.memory = EphemeralWorkingMemory(
            sliding_window_size=self.role_card.sliding_window_size
        )
        self.is_alive: bool = False

    def _on_spawn(self) -> None:
        """Lifecycle Hook 1: Instantiate ephemeral working memory and mark worker active."""
        self.memory = EphemeralWorkingMemory(
            sliding_window_size=self.role_card.sliding_window_size
        )
        self.is_alive = True

    def _on_tool_call(self, tool_call: ToolCallSpec) -> ToolResult:
        """Lifecycle Hook 2: Physically enforce RoleCard tool whitelist before Layer 1 execution."""
        check_cancelled()
        # 1. Physical Tool Whitelisting Gate
        if tool_call.tool_name not in self.role_card.tools_whitelist:
            return ToolResult(
                success=False,
                output=(
                    f"Tool '{tool_call.tool_name}' is forbidden for role '{self.role_card.role_id}'. "
                    f"Allowed tools: {list(self.role_card.tools_whitelist)}"
                ),
                error_code="PERMISSION_DENIED",
                execution_time_ms=0.0,
            )

        # 2. Argument preparation
        args = dict(tool_call.arguments)
        if tool_call.tool_name == "apply_patch" and "patches" in args:
            raw_patches = args["patches"]
            if isinstance(raw_patches, list):
                args["patches"] = [
                    PatchBlock.model_validate(p) if isinstance(p, dict) else p
                    for p in raw_patches
                ]

        # 3. Layer 1 Dispatch
        return self.tool_registry.execute(tool_call.tool_name, self.ctx, **args)

    def _on_terminate(self) -> None:
        """Lifecycle Hook 3: Wipe ephemeral memory and mark worker destroyed."""
        self.memory.clear()
        self.is_alive = False

    def _build_system_prompt(self) -> str:
        """Assemble system prompt containing role identity, directive, and tool specifications."""
        tools_desc = []
        if "read_file" in self.role_card.tools_whitelist:
            tools_desc.append("- read_file(path: str, start_line: int | None = 1, end_line: int | None = 250)")
        if "list_dir" in self.role_card.tools_whitelist:
            tools_desc.append("- list_dir(path: str = '.', max_depth: int = 2)")
        if "apply_patch" in self.role_card.tools_whitelist:
            tools_desc.append("- apply_patch(path: str, patches: list[dict[str, str]], create_if_missing: bool = False) -- apply atomic patches with {'search': '...', 'replace': '...'}")
        if "grep_search" in self.role_card.tools_whitelist:
            tools_desc.append("- grep_search(query: str, path: str = '.', is_regex: bool = False, max_results: int = 50)")
        if "find_files" in self.role_card.tools_whitelist:
            tools_desc.append("- find_files(pattern: str, path: str = '.', max_results: int = 100)")
        if "run_command" in self.role_card.tools_whitelist:
            tools_desc.append("- run_command(command: str, timeout_sec: int | None = None)")

        tools_str = "\n".join(tools_desc) if tools_desc else "(None)"

        return f"""# Identity: {self.role_card.persona_title} ({self.role_card.role_id})

# Behavioral Directive:
{self.role_card.system_directive}

# Permitted Tools Whitelist:
{tools_str}

# Protocol & Output Rules:
1. On each step, provide a ChassisStepDecision with 'thought', optional 'scratchpad_note', and 'action_type'.
2. If action_type == 'CALL_TOOL', provide 'tool_call' with 'tool_name' and 'arguments'.
3. If action_type == 'FINISH_TASK', provide 'final_report' with 'status', 'summary', 'discovered_facts', 'inquisitor_verdict', and 'error_details'.
4. Do not invent or hallucinate tools outside your whitelist.
"""

    def _build_user_prompt(self, step: int) -> str:
        """Assemble user prompt containing contract details, skeletons, diff, and memory."""
        contract = self.context_pkg.contract
        criteria_lines = "\n".join(f"  - {c}" for c in contract.acceptance_criteria) or "  (None)"
        target_files_str = ", ".join(contract.target_files) if contract.target_files else "(None)"

        skeletons_part = ""
        if self.context_pkg.target_file_skeletons:
            skel_lines = []
            for path, skel in self.context_pkg.target_file_skeletons.items():
                skel_lines.append(f"--- SKELETON: {path} ---\n{skel}")
            skeletons_part = "\n[TARGET FILE SKELETONS]\n" + "\n\n".join(skel_lines) + "\n"

        diff_part = ""
        if self.context_pkg.git_diff.strip():
            diff_part = f"\n[ACTIVE GIT DIFF]\n{self.context_pkg.git_diff}\n"

        constraints_part = ""
        if self.context_pkg.custom_constraints:
            c_lines = "\n".join(f"  - {c}" for c in self.context_pkg.custom_constraints)
            constraints_part = f"\n[CONSTRAINTS]\n{c_lines}\n"

        memory_part = self.memory.render_memory_context()

        return f"""# Assigned Task Contract
- Contract ID: {contract.contract_id}
- Role: {self.role_card.role_id}
- Objective: {contract.objective}
- Context Briefing: {contract.context_briefing}
- Target Files: {target_files_str}
- Test Command: {contract.test_command or '(None)'}
- Acceptance Criteria:
{criteria_lines}
{skeletons_part}{diff_part}{constraints_part}
# Working Memory State
{memory_part}

# Execution Budget
Step {step} of {self.role_card.max_steps} maximum steps.
"""

    def _build_final_report(self, draft: FinalReportDraft | None) -> WorkerReport:
        """Deterministically assemble final WorkerReport using memory as source of truth for mutations."""
        status = draft.status if draft else WorkerStatus.SUCCESS
        summary = draft.summary if draft else "Worker completed contract."
        discovered_facts = draft.discovered_facts if draft else []
        inquisitor_verdict = (
            draft.inquisitor_verdict if draft else InquisitorVerdict.NOT_APPLICABLE
        )
        if (
            self.role_card.role_id == "INQUISITOR"
            and inquisitor_verdict == InquisitorVerdict.NOT_APPLICABLE
        ):
            inquisitor_verdict = (
                InquisitorVerdict.APPROVED
                if status == WorkerStatus.SUCCESS
                else InquisitorVerdict.REJECTED
            )
        error_details = draft.error_details if draft else None

        # Deterministic modified_files populated strictly from successful apply_patch calls
        modified_files = list(self.memory.mutated_files)

        return WorkerReport(
            contract_id=self.context_pkg.contract.contract_id,
            worker_role=self.role_card.role_id,
            status=status,
            summary=summary,
            discovered_facts=discovered_facts,
            modified_files=modified_files,
            inquisitor_verdict=inquisitor_verdict,
            error_details=error_details,
        )

    def _build_step_exhaustion_report(self) -> WorkerReport:
        """Assemble structured failure report when max_steps budget is exceeded."""
        inquisitor_verdict = (
            InquisitorVerdict.REJECTED
            if self.role_card.role_id == "INQUISITOR"
            else InquisitorVerdict.NOT_APPLICABLE
        )
        return WorkerReport(
            contract_id=self.context_pkg.contract.contract_id,
            worker_role=self.role_card.role_id,
            status=WorkerStatus.FAILED,
            summary=f"Worker exhausted maximum step budget ({self.role_card.max_steps}) without completing the contract.",
            discovered_facts=[],
            modified_files=list(self.memory.mutated_files),
            inquisitor_verdict=inquisitor_verdict,
            error_details=f"Exhausted {self.role_card.max_steps} steps limit.",
        )

    async def execute(self) -> WorkerReport:
        """Execute the bounded ReAct loop until task completion, step exhaustion, or failure."""
        check_cancelled()
        self._on_spawn()
        try:
            for step in range(1, self.role_card.max_steps + 1):
                check_cancelled()
                system_prompt = self._build_system_prompt()
                user_prompt = self._build_user_prompt(step=step)

                messages = [
                    Message(role="system", content=system_prompt),
                    Message(role="user", content=user_prompt),
                ]

                decision, _ = await gateway_wait(self.gateway.generate_structured(
                    profile=self.role_card.model_profile,
                    messages=messages,
                    response_model=ChassisStepDecision,
                    task_id=self.ctx.task_id,
                ))

                # Persist scratchpad note if present
                if decision.scratchpad_note:
                    self.memory.add_note(decision.scratchpad_note)

                # Action Branch: FINISH_TASK
                if decision.action_type == "FINISH_TASK":
                    # Pre-Finish Verification Hook
                    if (
                        self.role_card.require_test_execution_if_configured
                        and bool(self.context_pkg.contract.test_command)
                        and len(self.memory.commands_executed) == 0
                        and step < self.role_card.max_steps
                    ):
                        auto_call = ToolCallSpec(
                            tool_name="run_command",
                            arguments={"command": self.context_pkg.contract.test_command},
                        )
                        res = await tool_wait(self._on_tool_call, auto_call)
                        self.memory.record_interaction(
                            step_number=step,
                            thought="[AUTO-PRE-FINISH-HOOK] Mandatory test execution intercepted before task completion.",
                            tool_name="run_command",
                            tool_args={"command": self.context_pkg.contract.test_command},
                            result=res,
                        )
                        continue

                    return self._build_final_report(decision.final_report)

                # Action Branch: CALL_TOOL
                if decision.action_type == "CALL_TOOL":
                    if not decision.tool_call:
                        fake_res = ToolResult(
                            success=False,
                            output="Error: action_type was CALL_TOOL but tool_call was not provided.",
                            error_code="INVALID_ARGUMENTS",
                            execution_time_ms=0.0,
                        )
                        self.memory.record_interaction(
                            step_number=step,
                            thought=decision.thought,
                            tool_name="unknown",
                            tool_args={},
                            result=fake_res,
                        )
                        continue

                    res = await tool_wait(self._on_tool_call, decision.tool_call)
                    self.memory.record_interaction(
                        step_number=step,
                        thought=decision.thought,
                        tool_name=decision.tool_call.tool_name,
                        tool_args=decision.tool_call.arguments,
                        result=res,
                    )

            # Max steps exhausted
            return self._build_step_exhaustion_report()

        finally:
            self._on_terminate()
