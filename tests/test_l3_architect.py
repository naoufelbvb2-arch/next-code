"""Verification & Acceptance Criteria test suite for Layer 3 (L3) - The Lead Architect.

Covers all 7 mandatory verification scenarios defined in Section 5 of L3_ARCHITECT_SPEC.md:
1. Rename Rollback Cleanup Polish (kernel/checkpoint.py)
2. Happy Path Orchestration Cycle (Scout -> Surgeon -> Inquisitor -> Complete)
3. Gate 4 Enforcement (Premature Completion Blocked Before Inquisitor)
4. Gate 3 Enforcement on Surgeon Failure (Auto-Rollback & Graveyard)
5. Gate 3 Enforcement on Inquisitor Rejection (Auto-Rollback & Re-Plan)
6. Gate 1 Enforcement (No Blind Surgery) & Max Rollbacks Abort
7. Live DeepSeek Architect Reasoning Integration (with live API key)
"""

import os
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest

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
from architect.dispatcher import ScriptedMockDispatcher
from architect.lead_loop import LeadArchitectLoop
from brain.schemas import PlanStep, StepStatus, TaskPhase
from kernel.checkpoint import GitShadowCheckpointer
from kernel.config import KernelConfig
from kernel.gateway.openai_compat import OpenAICompatibleProvider
from kernel.gateway.router import ModelGateway
from kernel.schemas import ExecutionContext, ModelProfile


# ==============================================================================
# Helper Mock HTTP Transport for Scripted Architect Decisions
# ==============================================================================
class MockLLMTransport(httpx.AsyncBaseTransport):
    """Mock HTTP transport returning controlled responses to test Architect decisions."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.call_count = 0
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        idx = min(self.call_count, len(self.responses) - 1)
        resp_data = self.responses[idx]
        self.call_count += 1
        return httpx.Response(200, json=resp_data)


def create_mock_gateway(decisions: list[ArchitectDecision]) -> ModelGateway:
    """Create a ModelGateway backed by a scripted sequence of ArchitectDecision responses."""
    responses = [
        {
            "choices": [{"message": {"content": d.model_dump_json()}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 60, "total_tokens": 180},
        }
        for d in decisions
    ]
    mock_client = httpx.AsyncClient(transport=MockLLMTransport(responses))
    provider = OpenAICompatibleProvider(client=mock_client)
    gateway = ModelGateway(config=KernelConfig())
    gateway.register_route(ModelProfile.REASONING, provider)
    return gateway


def init_git_repo(path: Path) -> None:
    """Initialize a clean Git repository in path with default committer identity."""
    subprocess.run(["git", "init"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(path), check=True)


# ==============================================================================
# Scenario 1: Rename Rollback Cleanup Polish (kernel/checkpoint.py)
# ==============================================================================
def test_rename_rollback_cleanup_polish(tmp_path: Path) -> None:
    """Scenario 1: Verify renaming a tracked file after a checkpoint restores original and deletes destination on rollback."""
    repo = tmp_path / "repo_rename"
    repo.mkdir()
    init_git_repo(repo)

    orig_file = repo / "module_orig.py"
    orig_file.write_text("def original(): pass\n", encoding="utf-8")
    subprocess.run(["git", "add", "module_orig.py"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), check=True)

    checkpointer = GitShadowCheckpointer(repo)
    cp_hash = checkpointer.create_checkpoint("before_rename")

    # Rename the file via git mv and add modifications
    renamed_file = repo / "module_renamed.py"
    subprocess.run(["git", "mv", "module_orig.py", "module_renamed.py"], cwd=str(repo), check=True)
    renamed_file.write_text("def renamed_and_modified(): pass\n", encoding="utf-8")

    assert not orig_file.exists()
    assert renamed_file.exists()

    # Roll back to checkpoint
    checkpointer.rollback(cp_hash)

    # Invariant: Original file is restored, and renamed destination is completely deleted!
    assert orig_file.exists()
    assert orig_file.read_text(encoding="utf-8") == "def original(): pass\n"
    assert not renamed_file.exists()


# ==============================================================================
# Scenario 2: Happy Path Orchestration Cycle (Scout -> Surgeon -> Inquisitor -> Complete)
# ==============================================================================
@pytest.mark.asyncio
async def test_happy_path_orchestration_cycle(tmp_path: Path) -> None:
    """Scenario 2: Verify full lifecycle Scout -> Surgeon -> Inquisitor -> Complete."""
    repo = tmp_path / "repo_happy"
    repo.mkdir()
    init_git_repo(repo)

    math_file = repo / "math_lib.py"
    math_file.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    subprocess.run(["git", "add", "math_lib.py"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-m", "initial math"], cwd=str(repo), check=True)

    # 1. Scripted Architect decisions
    decisions = [
        # Tick 1: Dispatch SCOUT
        ArchitectDecision(
            situation_assessment="Need to inspect math_lib.py to locate the bug.",
            mental_simulation=MentalSimulation(
                hypothesis="The add function returns subtraction.",
                predicted_side_effects=[],
                verification_strategy="Read math_lib.py line 1 to 5.",
            ),
            new_phase=TaskPhase.EXPLORING,
            updated_plan_steps=[
                PlanStep(step_id="s1", description="Inspect math_lib", assigned_role="SCOUT", target_files=["math_lib.py"])
            ],
            new_facts_to_record=[],
            next_action=ArchitectAction.DISPATCH_SCOUT,
            task_contract=TaskContract(
                contract_id="contract_scout_01",
                target_role="SCOUT",
                objective="Read math_lib.py and locate incorrect return operator.",
                target_files=["math_lib.py"],
                context_briefing="math_lib.py has an arithmetic bug.",
            ),
        ),
        # Tick 2: Dispatch SURGEON
        ArchitectDecision(
            situation_assessment="SCOUT confirmed add() has a subtraction operator at line 2.",
            mental_simulation=MentalSimulation(
                hypothesis="Replacing a - b with a + b will resolve the issue.",
                predicted_side_effects=["Dependents calling add() will now get addition."],
                verification_strategy="Run unit tests on math_lib.",
            ),
            new_phase=TaskPhase.EXECUTING,
            updated_plan_steps=[
                PlanStep(step_id="s2", description="Patch math_lib", assigned_role="SURGEON", target_files=["math_lib.py"])
            ],
            new_facts_to_record=[
                VerifiedFactInput(statement="add returns subtraction", evidence_file="math_lib.py", start_line=2, end_line=2)
            ],
            next_action=ArchitectAction.DISPATCH_SURGEON,
            task_contract=TaskContract(
                contract_id="contract_surgeon_01",
                target_role="SURGEON",
                objective="Replace 'return a - b' with 'return a + b' in math_lib.py.",
                target_files=["math_lib.py"],
                context_briefing="Fix operator at line 2.",
                acceptance_criteria=["add(2, 3) == 5"],
            ),
        ),
        # Tick 3: Dispatch INQUISITOR
        ArchitectDecision(
            situation_assessment="SURGEON completed patch. Inquisitor verification required.",
            mental_simulation=MentalSimulation(
                hypothesis="The diff in math_lib.py is minimal and correct.",
                predicted_side_effects=[],
                verification_strategy="Verify diff and test assertions.",
            ),
            new_phase=TaskPhase.VERIFYING,
            updated_plan_steps=[],
            new_facts_to_record=[],
            next_action=ArchitectAction.DISPATCH_INQUISITOR,
            task_contract=TaskContract(
                contract_id="contract_inq_01",
                target_role="INQUISITOR",
                objective="Verify git diff of math_lib.py.",
                target_files=["math_lib.py"],
                context_briefing="Verify fix.",
            ),
        ),
        # Tick 4: COMPLETE_TASK
        ArchitectDecision(
            situation_assessment="Inquisitor APPROVED the fix. Ready to complete.",
            mental_simulation=MentalSimulation(
                hypothesis="Goal fully satisfied.",
                predicted_side_effects=[],
                verification_strategy="Complete task.",
            ),
            new_phase=TaskPhase.COMPLETED,
            updated_plan_steps=[],
            new_facts_to_record=[],
            next_action=ArchitectAction.COMPLETE_TASK,
            final_response="Math library bug fixed and verified successfully.",
        ),
    ]

    gateway = create_mock_gateway(decisions)

    # 2. Scripted Worker Dispatcher
    async def dispatcher_handler(contract: TaskContract, ctx: ExecutionContext, git_diff: str) -> WorkerReport:
        if contract.target_role == "SCOUT":
            return WorkerReport(
                contract_id=contract.contract_id,
                worker_role="SCOUT",
                status=WorkerStatus.SUCCESS,
                summary="Located subtraction bug on line 2.",
                discovered_facts=[
                    VerifiedFactInput(statement="Line 2 has return a - b", evidence_file="math_lib.py", start_line=2, end_line=2)
                ],
            )
        elif contract.target_role == "SURGEON":
            # Simulate mutation on disk
            math_file.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
            return WorkerReport(
                contract_id=contract.contract_id,
                worker_role="SURGEON",
                status=WorkerStatus.SUCCESS,
                summary="Patched math_lib.py successfully.",
                modified_files=["math_lib.py"],
            )
        elif contract.target_role == "INQUISITOR":
            assert "return a + b" in git_diff
            return WorkerReport(
                contract_id=contract.contract_id,
                worker_role="INQUISITOR",
                status=WorkerStatus.SUCCESS,
                summary="Verified diff. Zero regressions.",
                inquisitor_verdict=InquisitorVerdict.APPROVED,
            )
        return WorkerReport(contract_id=contract.contract_id, worker_role=contract.target_role, status=WorkerStatus.SUCCESS, summary="ok")

    dispatcher = ScriptedMockDispatcher(handler=dispatcher_handler)

    # 3. Run LeadArchitectLoop
    loop = LeadArchitectLoop(
        workspace_root=repo,
        gateway=gateway,
        dispatcher=dispatcher,
        task_id="task_happy_01",
        task_goal="Fix add() operator in math_lib.py",
        max_ticks=10,
    )

    outcome = await loop.run()

    assert outcome.success is True
    assert outcome.final_phase == TaskPhase.COMPLETED
    assert outcome.rollbacks_triggered == 0
    assert outcome.total_ticks == 4
    assert "return a + b" in math_file.read_text(encoding="utf-8")
    assert loop.blackboard.state.phase == TaskPhase.COMPLETED


# ==============================================================================
# Scenario 3: Gate 4 Enforcement (Premature Completion Blocked Before Inquisitor)
# ==============================================================================
@pytest.mark.asyncio
async def test_gate_4_premature_completion_blocked(tmp_path: Path) -> None:
    """Scenario 3: Verify Gate 4 intercepts premature COMPLETE_TASK after surgery and forces INQUISITOR."""
    repo = tmp_path / "repo_gate4"
    repo.mkdir()
    init_git_repo(repo)

    auth_file = repo / "auth.py"
    auth_file.write_text("TOKEN = 'old'\n", encoding="utf-8")
    subprocess.run(["git", "add", "auth.py"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-m", "init auth"], cwd=str(repo), check=True)

    decisions = [
        # Tick 1: SURGEON modifies auth.py
        ArchitectDecision(
            situation_assessment="Need to update TOKEN.",
            mental_simulation=MentalSimulation(hypothesis="Update token", predicted_side_effects=[], verification_strategy="Patch"),
            new_phase=TaskPhase.EXECUTING,
            updated_plan_steps=[],
            new_facts_to_record=[],
            next_action=ArchitectAction.DISPATCH_SURGEON,
            task_contract=TaskContract(
                contract_id="c_surg", target_role="SURGEON", objective="Update token", target_files=["auth.py"], context_briefing="auth"
            ),
        ),
        # Tick 2: LLM prematurely outputs COMPLETE_TASK! (Gate 4 will intercept this and override to DISPATCH_INQUISITOR)
        ArchitectDecision(
            situation_assessment="I think we are done immediately!",
            mental_simulation=MentalSimulation(hypothesis="Done", predicted_side_effects=[], verification_strategy="None"),
            new_phase=TaskPhase.COMPLETED,
            updated_plan_steps=[],
            new_facts_to_record=[],
            next_action=ArchitectAction.COMPLETE_TASK,
            final_response="Completed prematurely.",
        ),
        # Tick 3: After Inquisitor approved in tick 2, now COMPLETE_TASK is legitimately allowed
        ArchitectDecision(
            situation_assessment="Inquisitor approved. Now genuinely complete.",
            mental_simulation=MentalSimulation(hypothesis="Verified", predicted_side_effects=[], verification_strategy="Done"),
            new_phase=TaskPhase.COMPLETED,
            updated_plan_steps=[],
            new_facts_to_record=[],
            next_action=ArchitectAction.COMPLETE_TASK,
            final_response="Successfully completed after mandatory Inquisitor.",
        ),
    ]

    gateway = create_mock_gateway(decisions)

    async def dispatcher_handler(contract: TaskContract, ctx: ExecutionContext, git_diff: str) -> WorkerReport:
        if contract.target_role == "SURGEON":
            auth_file.write_text("TOKEN = 'new'\n", encoding="utf-8")
            return WorkerReport(
                contract_id=contract.contract_id,
                worker_role="SURGEON",
                status=WorkerStatus.SUCCESS,
                summary="Token updated.",
                modified_files=["auth.py"],
            )
        elif contract.target_role == "INQUISITOR":
            assert "TOKEN = 'new'" in git_diff
            return WorkerReport(
                contract_id=contract.contract_id,
                worker_role="INQUISITOR",
                status=WorkerStatus.SUCCESS,
                summary="Verified token change.",
                inquisitor_verdict=InquisitorVerdict.APPROVED,
            )
        return WorkerReport(contract_id=contract.contract_id, worker_role=contract.target_role, status=WorkerStatus.SUCCESS, summary="ok")

    dispatcher = ScriptedMockDispatcher(handler=dispatcher_handler)

    loop = LeadArchitectLoop(
        workspace_root=repo,
        gateway=gateway,
        dispatcher=dispatcher,
        task_id="task_gate4_01",
        task_goal="Update auth token",
        max_ticks=10,
    )

    outcome = await loop.run()

    assert outcome.success is True
    # Dispatched calls: call 0 was SURGEON, call 1 was the auto-intercepted INQUISITOR!
    assert len(dispatcher.dispatched_calls) == 2
    assert dispatcher.dispatched_calls[0]["contract"].target_role == "SURGEON"
    assert dispatcher.dispatched_calls[1]["contract"].target_role == "INQUISITOR"
    assert "Gate 4 Overridden" in loop.last_worker_report.summary or outcome.total_ticks == 3


# ==============================================================================
# Scenario 4: Gate 3 Enforcement on Surgeon Failure (Auto-Rollback & Graveyard)
# ==============================================================================
@pytest.mark.asyncio
async def test_gate_3_surgeon_failure_auto_rollback(tmp_path: Path) -> None:
    """Scenario 4: Verify Gate 3 triggers rollback and graveyard record when Surgeon fails."""
    repo = tmp_path / "repo_gate3_surg"
    repo.mkdir()
    init_git_repo(repo)

    core_file = repo / "core.py"
    core_file.write_text("STATUS = 'STABLE'\n", encoding="utf-8")
    subprocess.run(["git", "add", "core.py"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-m", "init core"], cwd=str(repo), check=True)

    decisions = [
        # Tick 1: SURGEON attempts flawed patch
        ArchitectDecision(
            situation_assessment="Modifying core status.",
            mental_simulation=MentalSimulation(
                hypothesis="Change STATUS to TURBO", predicted_side_effects=[], verification_strategy="Patch"
            ),
            new_phase=TaskPhase.EXECUTING,
            updated_plan_steps=[],
            new_facts_to_record=[],
            next_action=ArchitectAction.DISPATCH_SURGEON,
            task_contract=TaskContract(
                contract_id="c_fail_surg", target_role="SURGEON", objective="Change status", target_files=["core.py"], context_briefing="core"
            ),
        ),
        # Tick 2: Architect decides to ABORT after seeing the failure in the graveyard
        ArchitectDecision(
            situation_assessment="Surgeon failed and rolled back.",
            mental_simulation=MentalSimulation(hypothesis="Abort", predicted_side_effects=[], verification_strategy="Abort"),
            new_phase=TaskPhase.FAILED,
            updated_plan_steps=[],
            new_facts_to_record=[],
            next_action=ArchitectAction.ABORT_TASK,
            final_response="Aborted due to surgeon failure.",
        ),
    ]

    gateway = create_mock_gateway(decisions)

    async def dispatcher_handler(contract: TaskContract, ctx: ExecutionContext, git_diff: str) -> WorkerReport:
        # Surgeon corrupts file on disk then fails
        core_file.write_text("STATUS = 'CORRUPTED_PARTIAL'\n", encoding="utf-8")
        return WorkerReport(
            contract_id=contract.contract_id,
            worker_role="SURGEON",
            status=WorkerStatus.FAILED,
            summary="SyntaxError encountered during AST patch application.",
            error_details="SyntaxError: invalid syntax at line 1",
            modified_files=["core.py"],
        )

    dispatcher = ScriptedMockDispatcher(handler=dispatcher_handler)

    loop = LeadArchitectLoop(
        workspace_root=repo,
        gateway=gateway,
        dispatcher=dispatcher,
        task_id="task_gate3_surg",
        task_goal="Upgrade core to turbo",
        max_ticks=10,
    )

    outcome = await loop.run()

    assert outcome.success is False
    assert outcome.rollbacks_triggered == 1
    # File on disk was restored to STABLE!
    assert core_file.read_text(encoding="utf-8") == "STATUS = 'STABLE'\n"

    # Blackboard recorded failed hypothesis in the graveyard
    assert len(loop.blackboard.state.failed_hypotheses) == 1
    fh = loop.blackboard.state.failed_hypotheses[0]
    assert fh.hypothesis == "Change STATUS to TURBO"
    assert "SyntaxError" in fh.failure_reason


# ==============================================================================
# Scenario 5: Gate 3 Enforcement on Inquisitor Rejection (Auto-Rollback & Re-Plan)
# ==============================================================================
@pytest.mark.asyncio
async def test_gate_3_inquisitor_rejection_auto_rollback(tmp_path: Path) -> None:
    """Scenario 5: Verify Gate 3 rolls back disk changes on Inquisitor rejection and allows re-planning."""
    repo = tmp_path / "repo_gate3_inq"
    repo.mkdir()
    init_git_repo(repo)

    calc_file = repo / "calc.py"
    calc_file.write_text("def mul(a, b): return a + b\n", encoding="utf-8")
    subprocess.run(["git", "add", "calc.py"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-m", "init calc"], cwd=str(repo), check=True)

    decisions = [
        # Tick 1: SURGEON modifies calc.py with buggy implementation
        ArchitectDecision(
            situation_assessment="Fix multiplication.",
            mental_simulation=MentalSimulation(hypothesis="Use a * b + 1", predicted_side_effects=[], verification_strategy="Check"),
            new_phase=TaskPhase.EXECUTING,
            updated_plan_steps=[],
            new_facts_to_record=[],
            next_action=ArchitectAction.DISPATCH_SURGEON,
            task_contract=TaskContract(
                contract_id="c_surg_buggy", target_role="SURGEON", objective="Implement mul", target_files=["calc.py"], context_briefing="calc"
            ),
        ),
        # Tick 2: INQUISITOR checks and rejects
        ArchitectDecision(
            situation_assessment="Verify buggy mul.",
            mental_simulation=MentalSimulation(hypothesis="Check diff", predicted_side_effects=[], verification_strategy="Inq"),
            new_phase=TaskPhase.VERIFYING,
            updated_plan_steps=[],
            new_facts_to_record=[],
            next_action=ArchitectAction.DISPATCH_INQUISITOR,
            task_contract=TaskContract(
                contract_id="c_inq_1", target_role="INQUISITOR", objective="Verify mul", target_files=["calc.py"], context_briefing="calc"
            ),
        ),
        # Tick 3: After rollback, SURGEON applies correct implementation
        ArchitectDecision(
            situation_assessment="Previous hypothesis failed in graveyard. Re-trying with pure a * b.",
            mental_simulation=MentalSimulation(hypothesis="Use pure a * b", predicted_side_effects=[], verification_strategy="Inq"),
            new_phase=TaskPhase.EXECUTING,
            updated_plan_steps=[],
            new_facts_to_record=[],
            next_action=ArchitectAction.DISPATCH_SURGEON,
            task_contract=TaskContract(
                contract_id="c_surg_correct", target_role="SURGEON", objective="Correct mul", target_files=["calc.py"], context_briefing="calc"
            ),
        ),
        # Tick 4: INQUISITOR approves
        ArchitectDecision(
            situation_assessment="Verify correct mul.",
            mental_simulation=MentalSimulation(hypothesis="Verify diff", predicted_side_effects=[], verification_strategy="Inq"),
            new_phase=TaskPhase.VERIFYING,
            updated_plan_steps=[],
            new_facts_to_record=[],
            next_action=ArchitectAction.DISPATCH_INQUISITOR,
            task_contract=TaskContract(
                contract_id="c_inq_2", target_role="INQUISITOR", objective="Verify mul", target_files=["calc.py"], context_briefing="calc"
            ),
        ),
        # Tick 5: COMPLETE_TASK
        ArchitectDecision(
            situation_assessment="Inquisitor APPROVED. Done.",
            mental_simulation=MentalSimulation(hypothesis="Done", predicted_side_effects=[], verification_strategy="Done"),
            new_phase=TaskPhase.COMPLETED,
            updated_plan_steps=[],
            new_facts_to_record=[],
            next_action=ArchitectAction.COMPLETE_TASK,
            final_response="Multiplication fixed accurately.",
        ),
    ]

    gateway = create_mock_gateway(decisions)

    async def dispatcher_handler(contract: TaskContract, ctx: ExecutionContext, git_diff: str) -> WorkerReport:
        if contract.contract_id == "c_surg_buggy":
            calc_file.write_text("def mul(a, b): return a * b + 1\n", encoding="utf-8")
            return WorkerReport(contract_id=contract.contract_id, worker_role="SURGEON", status=WorkerStatus.SUCCESS, summary="Buggy edit", modified_files=["calc.py"])
        elif contract.contract_id == "c_inq_1":
            return WorkerReport(
                contract_id=contract.contract_id,
                worker_role="INQUISITOR",
                status=WorkerStatus.FAILED,
                summary="Rejected: off by one error.",
                inquisitor_verdict=InquisitorVerdict.REJECTED,
                error_details="mul(2, 2) returned 5 instead of 4",
            )
        elif contract.contract_id == "c_surg_correct":
            # Note: Before this call, Gate 3 rolled calc_file back to "def mul(a, b): return a + b\n"!
            assert "return a * b + 1" not in calc_file.read_text(encoding="utf-8")
            calc_file.write_text("def mul(a, b): return a * b\n", encoding="utf-8")
            return WorkerReport(contract_id=contract.contract_id, worker_role="SURGEON", status=WorkerStatus.SUCCESS, summary="Correct edit", modified_files=["calc.py"])
        elif contract.contract_id == "c_inq_2":
            return WorkerReport(
                contract_id=contract.contract_id,
                worker_role="INQUISITOR",
                status=WorkerStatus.SUCCESS,
                summary="Approved: 2 * 2 == 4",
                inquisitor_verdict=InquisitorVerdict.APPROVED,
            )
        return WorkerReport(contract_id=contract.contract_id, worker_role=contract.target_role, status=WorkerStatus.SUCCESS, summary="ok")

    dispatcher = ScriptedMockDispatcher(handler=dispatcher_handler)

    loop = LeadArchitectLoop(
        workspace_root=repo,
        gateway=gateway,
        dispatcher=dispatcher,
        task_id="task_gate3_inq",
        task_goal="Fix multiplication in calc.py",
        max_ticks=10,
    )

    outcome = await loop.run()

    assert outcome.success is True
    assert outcome.rollbacks_triggered == 1
    assert calc_file.read_text(encoding="utf-8") == "def mul(a, b): return a * b\n"
    assert len(loop.blackboard.state.failed_hypotheses) == 1
    assert "off by one error" in loop.blackboard.state.failed_hypotheses[0].failure_reason


# ==============================================================================
# Scenario 6: Gate 1 Enforcement (No Blind Surgery) & Max Rollbacks Abort
# ==============================================================================
@pytest.mark.asyncio
async def test_gate_1_and_max_rollbacks_abort(tmp_path: Path) -> None:
    """Scenario 6: Verify Gate 1 blocks DISPATCH_SURGEON with empty target_files, and max rollbacks aborts."""
    repo = tmp_path / "repo_gate1"
    repo.mkdir()
    init_git_repo(repo)

    dummy_file = repo / "dummy.txt"
    dummy_file.write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "dummy.txt"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-m", "init dummy"], cwd=str(repo), check=True)

    decisions = [
        # Tick 1: Blind surgery attempt (target_files is empty) -> Gate 1 blocks!
        ArchitectDecision(
            situation_assessment="I want to edit something blind.",
            mental_simulation=MentalSimulation(hypothesis="Blind edit", predicted_side_effects=[], verification_strategy="None"),
            new_phase=TaskPhase.EXECUTING,
            updated_plan_steps=[],
            new_facts_to_record=[],
            next_action=ArchitectAction.DISPATCH_SURGEON,
            task_contract=TaskContract(
                contract_id="c_blind", target_role="SURGEON", objective="Blind", target_files=[], context_briefing="none"
            ),
        ),
        # Tick 2: Surgery 1 that fails
        ArchitectDecision(
            situation_assessment="Surgery 1 with target file.",
            mental_simulation=MentalSimulation(hypothesis="Edit 1", predicted_side_effects=[], verification_strategy="Test"),
            new_phase=TaskPhase.EXECUTING,
            updated_plan_steps=[],
            new_facts_to_record=[],
            next_action=ArchitectAction.DISPATCH_SURGEON,
            task_contract=TaskContract(
                contract_id="c_s1", target_role="SURGEON", objective="Edit", target_files=["dummy.txt"], context_briefing="dummy"
            ),
        ),
        # Tick 3: Surgery 2 that fails -> hits max_rollbacks = 2!
        ArchitectDecision(
            situation_assessment="Surgery 2 with target file.",
            mental_simulation=MentalSimulation(hypothesis="Edit 2", predicted_side_effects=[], verification_strategy="Test"),
            new_phase=TaskPhase.EXECUTING,
            updated_plan_steps=[],
            new_facts_to_record=[],
            next_action=ArchitectAction.DISPATCH_SURGEON,
            task_contract=TaskContract(
                contract_id="c_s2", target_role="SURGEON", objective="Edit", target_files=["dummy.txt"], context_briefing="dummy"
            ),
        ),
    ]

    gateway = create_mock_gateway(decisions)

    dispatcher = ScriptedMockDispatcher(
        reports=[
            WorkerReport(contract_id="c_s1", worker_role="SURGEON", status=WorkerStatus.FAILED, summary="Fail 1"),
            WorkerReport(contract_id="c_s2", worker_role="SURGEON", status=WorkerStatus.FAILED, summary="Fail 2"),
        ]
    )

    loop = LeadArchitectLoop(
        workspace_root=repo,
        gateway=gateway,
        dispatcher=dispatcher,
        task_id="task_gate1_abort",
        task_goal="Test blind surgery and max rollbacks",
        max_ticks=10,
        max_rollbacks=2,
    )

    outcome = await loop.run()

    # Invariant: c_blind was never sent to dispatcher!
    dispatched_ids = [call["contract"].contract_id for call in dispatcher.dispatched_calls]
    assert "c_blind" not in dispatched_ids
    assert dispatched_ids == ["c_s1", "c_s2"]

    # Invariant: Loop aborted when max_rollbacks (2) was reached
    assert outcome.success is False
    assert outcome.final_phase == TaskPhase.FAILED
    assert outcome.rollbacks_triggered == 2
    assert "Exceeded maximum allowed rollbacks" in outcome.summary


# ==============================================================================
# Scenario 7: Live DeepSeek Architect Reasoning Integration
# ==============================================================================
@pytest.mark.asyncio
async def test_live_deepseek_architect_reasoning(tmp_path: Path) -> None:
    """Scenario 7: Live end-to-end integration test with DeepSeek API generating valid ArchitectDecision."""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        pytest.skip("DEEPSEEK_API_KEY is not configured.")

    repo = tmp_path / "repo_live"
    repo.mkdir()
    init_git_repo(repo)

    svc_file = repo / "service.py"
    svc_file.write_text("def get_status():\n    return 'inactive'\n", encoding="utf-8")
    subprocess.run(["git", "add", "service.py"], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-m", "init service"], cwd=str(repo), check=True)

    config = KernelConfig(deepseek_api_key=api_key)
    gateway = ModelGateway(config=config)

    # Use a mock dispatcher that approves whatever the architect sends
    dispatcher = ScriptedMockDispatcher(
        reports=[
            WorkerReport(
                contract_id="live_scout_contract",
                worker_role="SCOUT",
                status=WorkerStatus.SUCCESS,
                summary="Inspected service.py and verified status returns 'inactive'.",
                discovered_facts=[
                    VerifiedFactInput(
                        statement="get_status returns inactive",
                        evidence_file="service.py",
                        start_line=1,
                        end_line=2,
                    )
                ],
            )
        ]
    )

    loop = LeadArchitectLoop(
        workspace_root=repo,
        gateway=gateway,
        dispatcher=dispatcher,
        task_id="live_deepseek_arch_test",
        task_goal="Change get_status to return active instead of inactive",
        max_ticks=1,
    )

    # Run exactly 1 tick
    outcome = await loop.run()

    # The 1 tick should complete without crashing or schema validation errors
    assert outcome.task_id == "live_deepseek_arch_test"
    assert outcome.total_ticks == 1
    # Check that Blackboard successfully transitioned phase based on DeepSeek's structured output
    assert loop.blackboard.state.phase in (TaskPhase.EXPLORING, TaskPhase.PLANNING, TaskPhase.EXECUTING, TaskPhase.FAILED)
