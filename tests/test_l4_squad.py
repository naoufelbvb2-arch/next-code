"""Comprehensive verification suite for Layer 4 (L4) - Standard Agent Template & Ephemeral Squads and AgentOS."""

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest

from agent_os import AgentOS
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
from brain.repo_graph import RepoGraph
from brain.schemas import TaskPhase
from kernel.config import KernelConfig
from kernel.gateway.openai_compat import OpenAICompatibleProvider
from kernel.gateway.router import ModelGateway
from kernel.schemas import AgentRole, ExecutionContext, ModelProfile, PatchBlock
from kernel.tools.registry import ToolRegistry
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
    RoleCard,
    ToolCallSpec,
)


# ==============================================================================
# Test Fixtures & Mock Infrastructures
# ==============================================================================
class SequentialMockTransport(httpx.AsyncBaseTransport):
    """Async transport serving a pre-configured sequence of JSON responses."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.responses:
            resp_data = self.responses.pop(0)
        else:
            resp_data = {
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            }
        return httpx.Response(200, json=resp_data)


def create_mock_gateway(responses: list[Any]) -> ModelGateway:
    """Create a ModelGateway backed by a sequential list of Pydantic models or dicts."""
    formatted_responses = []
    for item in responses:
        if hasattr(item, "model_dump_json"):
            content = item.model_dump_json()
        elif isinstance(item, dict) and "choices" not in item:
            content = json.dumps(item)
        elif isinstance(item, str):
            content = item
        else:
            formatted_responses.append(item)
            continue

        formatted_responses.append(
            {
                "choices": [{"message": {"content": content}}],
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 60,
                    "total_tokens": 180,
                },
            }
        )

    transport = SequentialMockTransport(formatted_responses)
    client = httpx.AsyncClient(transport=transport)
    provider = OpenAICompatibleProvider(client=client)
    gateway = ModelGateway(config=KernelConfig())
    gateway.register_route(ModelProfile.REASONING, provider)
    gateway.register_route(ModelProfile.FAST_WORKER, provider)
    gateway.register_route(ModelProfile.LOCAL_CODER, provider)
    return gateway


def init_git_repo(path: Path) -> None:
    """Initialize a clean Git repository in path with default committer identity."""
    subprocess.run(["git", "init"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(path), check=True)


# ==============================================================================
# Scenario 1: Chassis Physical Tool Whitelist Enforcement
# ==============================================================================
def test_chassis_physical_tool_whitelist_enforcement(tmp_path: Path) -> None:
    """Scenario 1: Verify that tools outside a RoleCard's whitelist are rejected with PERMISSION_DENIED."""
    workspace = tmp_path / "workspace_test1"
    workspace.mkdir()
    sample_file = workspace / "sample.py"
    sample_file.write_text("x = 1\n", encoding="utf-8")

    ctx = ExecutionContext(
        task_id="t1",
        role=AgentRole.SCOUT,
        workspace_root=workspace,
    )
    gateway = create_mock_gateway([])
    registry = ToolRegistry()

    # 1. SCOUT Whitelist: ("read_file", "list_dir", "grep_search", "find_files")
    scout_pkg = ContextPackage(
        contract=TaskContract(
            contract_id="c_scout",
            target_role="SCOUT",
            objective="Scout",
            context_briefing="Recon",
        )
    )
    scout_chassis = StandardAgentChassis(
        role_card=SCOUT_ROLE_CARD,
        context_pkg=scout_pkg,
        ctx=ctx,
        gateway=gateway,
        tool_registry=registry,
    )

    # Calling apply_patch must be rejected immediately by chassis
    res_patch = scout_chassis._on_tool_call(
        ToolCallSpec(
            tool_name="apply_patch",
            arguments={"path": "sample.py", "patches": [{"search": "x = 1", "replace": "x = 2"}]},
        )
    )
    assert not res_patch.success
    assert res_patch.error_code == "PERMISSION_DENIED"
    assert "Tool 'apply_patch' is forbidden for role 'SCOUT'" in res_patch.output

    # Calling run_command must be rejected immediately by chassis
    res_shell = scout_chassis._on_tool_call(
        ToolCallSpec(
            tool_name="run_command",
            arguments={"command": "echo forbidden"},
        )
    )
    assert not res_shell.success
    assert res_shell.error_code == "PERMISSION_DENIED"
    assert "Tool 'run_command' is forbidden for role 'SCOUT'" in res_shell.output

    # Calling read_file (whitelisted) must succeed
    res_read = scout_chassis._on_tool_call(
        ToolCallSpec(
            tool_name="read_file",
            arguments={"path": "sample.py"},
        )
    )
    assert res_read.success
    assert "x = 1" in res_read.output

    # 2. INQUISITOR Whitelist: ("read_file", "list_dir", "grep_search", "find_files", "run_command")
    inq_pkg = ContextPackage(
        contract=TaskContract(
            contract_id="c_inq",
            target_role="INQUISITOR",
            objective="Inquire",
            context_briefing="QA",
        )
    )
    inq_ctx = ExecutionContext(
        task_id="t1_inq",
        role=AgentRole.INQUISITOR,
        workspace_root=workspace,
    )
    inq_chassis = StandardAgentChassis(
        role_card=INQUISITOR_ROLE_CARD,
        context_pkg=inq_pkg,
        ctx=inq_ctx,
        gateway=gateway,
        tool_registry=registry,
    )

    # Calling apply_patch on INQUISITOR must be rejected
    res_inq_patch = inq_chassis._on_tool_call(
        ToolCallSpec(
            tool_name="apply_patch",
            arguments={"path": "sample.py", "patches": [{"search": "x = 1", "replace": "x = 2"}]},
        )
    )
    assert not res_inq_patch.success
    assert res_inq_patch.error_code == "PERMISSION_DENIED"
    assert "Tool 'apply_patch' is forbidden for role 'INQUISITOR'" in res_inq_patch.output


# ==============================================================================
# Scenario 2: Sliding-Window Memory Compression & Ephemeral Destruction
# ==============================================================================
@pytest.mark.asyncio
async def test_sliding_window_memory_compression_and_ephemeral_destruction(tmp_path: Path) -> None:
    """Scenario 2: Verify sliding window context compression and memory wiping on chassis termination."""
    workspace = tmp_path / "workspace_test2"
    workspace.mkdir()

    # 1. Direct EphemeralWorkingMemory testing with sliding_window_size=2
    memory = EphemeralWorkingMemory(sliding_window_size=2)
    memory.add_note("Discovered potential bug in line 42")

    from kernel.schemas import ToolResult

    # Feed 5 sequential steps
    for i in range(1, 6):
        res = ToolResult(
            success=True,
            output=f"Output line A for step {i}\nOutput line B for step {i}\nOutput line C for step {i}",
            execution_time_ms=5.0,
        )
        memory.record_interaction(
            step_number=i,
            thought=f"Executing step {i}",
            tool_name="read_file",
            tool_args={"path": f"file_{i}.py"},
            result=res,
        )

    rendered = memory.render_memory_context()

    # Steps 1, 2, and 3 must be compressed
    assert "[Step 1 - COMPRESSED]" in rendered
    assert "[Step 2 - COMPRESSED]" in rendered
    assert "[Step 3 - COMPRESSED]" in rendered

    # Steps 4 and 5 must retain FULL OUTPUT
    assert "[Step 4 - FULL OUTPUT]" in rendered
    assert "[Step 5 - FULL OUTPUT]" in rendered
    assert "Output line A for step 4" in rendered
    assert "Output line A for step 5" in rendered

    # Scratchpad notes must be preserved intact
    assert "Discovered potential bug in line 42" in rendered

    # 2. Ephemeral Destruction via StandardAgentChassis.execute()
    finish_decision = ChassisStepDecision(
        thought="Task finished.",
        action_type="FINISH_TASK",
        final_report=FinalReportDraft(
            status=WorkerStatus.SUCCESS,
            summary="Done",
            discovered_facts=[],
        ),
    )
    gateway = create_mock_gateway([finish_decision])
    ctx = ExecutionContext(
        task_id="t2_destruct",
        role=AgentRole.SCOUT,
        workspace_root=workspace,
    )
    contract = TaskContract(
        contract_id="c_destruct",
        target_role="SCOUT",
        objective="Inspect",
        context_briefing="Briefing",
    )
    context_pkg = ContextPackage(contract=contract)

    chassis = StandardAgentChassis(
        role_card=SCOUT_ROLE_CARD,
        context_pkg=context_pkg,
        ctx=ctx,
        gateway=gateway,
        tool_registry=ToolRegistry(),
    )

    report = await chassis.execute()
    assert report.status == WorkerStatus.SUCCESS

    # Verification: Chassis must be dead and memory completely wiped
    assert chassis.is_alive is False
    assert len(chassis.memory.history) == 0
    assert len(chassis.memory.scratchpad) == 0
    assert len(chassis.memory.mutated_files) == 0
    assert len(chassis.memory.commands_executed) == 0


# ==============================================================================
# Scenario 3: Mandatory Test Execution Hook & Truthful Modified Files Ledger
# ==============================================================================
@pytest.mark.asyncio
async def test_mandatory_test_execution_hook_and_truthful_modified_files_ledger(tmp_path: Path) -> None:
    """Scenario 3: Verify SURGEON auto-runs test_command if omitted and truthfully reports modified_files."""
    workspace = tmp_path / "workspace_test3"
    workspace.mkdir()
    calc_file = workspace / "calc.py"
    calc_file.write_text("def add(a, b): return a + b\n", encoding="utf-8")

    ctx = ExecutionContext(
        task_id="t3_surgeon",
        role=AgentRole.SURGEON,
        workspace_root=workspace,
        allowed_files=frozenset({"calc.py"}),
    )

    contract = TaskContract(
        contract_id="c_surg_hook",
        target_role="SURGEON",
        objective="Patch calc.py and verify",
        target_files=["calc.py"],
        context_briefing="Add comment",
        test_command="python -c \"print('test execution ok')\"",
    )
    context_pkg = ContextPackage(contract=contract)

    # Step 1: Patch file
    step1 = ChassisStepDecision(
        thought="Patching calc.py",
        action_type="CALL_TOOL",
        tool_call=ToolCallSpec(
            tool_name="apply_patch",
            arguments={
                "path": "calc.py",
                "patches": [{"search": "return a + b", "replace": "return a + b  # patched"}],
            },
        ),
    )

    # Step 2: Premature finish without running test_command!
    step2_premature = ChassisStepDecision(
        thought="Premature finish attempt without test",
        action_type="FINISH_TASK",
        final_report=FinalReportDraft(
            status=WorkerStatus.SUCCESS,
            summary="Premature finish",
            discovered_facts=[],
        ),
    )

    # Step 3: Legitimate finish after test execution has run
    step3_legit = ChassisStepDecision(
        thought="Test inspected, now completing task",
        action_type="FINISH_TASK",
        final_report=FinalReportDraft(
            status=WorkerStatus.SUCCESS,
            summary="Legitimate finish",
            discovered_facts=[],
        ),
    )

    gateway = create_mock_gateway([step1, step2_premature, step3_legit])
    chassis = StandardAgentChassis(
        role_card=SURGEON_ROLE_CARD,
        context_pkg=context_pkg,
        ctx=ctx,
        gateway=gateway,
        tool_registry=ToolRegistry(),
    )

    report = await chassis.execute()

    assert report.status == WorkerStatus.SUCCESS
    assert report.summary == "Legitimate finish"
    # Truthful modified_files ledger check: must contain "calc.py"
    assert report.modified_files == ["calc.py"]
    # Check that the patch was indeed applied to disk
    assert "return a + b  # patched" in calc_file.read_text(encoding="utf-8")


# ==============================================================================
# Scenario 4: Step Budget Exhaustion Handling
# ==============================================================================
@pytest.mark.asyncio
async def test_step_budget_exhaustion_handling(tmp_path: Path) -> None:
    """Scenario 4: Verify chassis terminates with FAILED status and cleans up memory when max_steps is exhausted."""
    workspace = tmp_path / "workspace_test4"
    workspace.mkdir()
    sample = workspace / "sample.py"
    sample.write_text("print('hello')\n", encoding="utf-8")

    # Custom role card with max_steps=3
    role_card = get_role_card("SCOUT", max_steps_override=3)
    assert role_card.max_steps == 3

    ctx = ExecutionContext(
        task_id="t4_exhaust",
        role=AgentRole.SCOUT,
        workspace_root=workspace,
    )
    context_pkg = ContextPackage(
        contract=TaskContract(
            contract_id="c_exhaust",
            target_role="SCOUT",
            objective="Endless reading",
            context_briefing="Loop",
        )
    )

    # Mock gateway returning endless read_file calls
    loop_decision = ChassisStepDecision(
        thought="Reading sample.py again",
        action_type="CALL_TOOL",
        tool_call=ToolCallSpec(
            tool_name="read_file",
            arguments={"path": "sample.py"},
        ),
    )
    gateway = create_mock_gateway([loop_decision, loop_decision, loop_decision, loop_decision])

    chassis = StandardAgentChassis(
        role_card=role_card,
        context_pkg=context_pkg,
        ctx=ctx,
        gateway=gateway,
        tool_registry=ToolRegistry(),
    )

    report = await chassis.execute()

    assert report.status == WorkerStatus.FAILED
    assert "exhausted maximum step budget (3)" in report.summary
    assert chassis.is_alive is False
    assert len(chassis.memory.history) == 0


# ==============================================================================
# Scenario 5: Full 4-Layer End-to-End Autonomous Bugfix Cycle (AgentOS.execute_task)
# ==============================================================================
@pytest.mark.asyncio
async def test_end_to_end_autonomous_bugfix_cycle(tmp_path: Path) -> None:
    """Scenario 5: Full 4-layer autonomous bugfix cycle: Scout -> Surgeon -> Inquisitor -> Complete."""
    repo = tmp_path / "repo_bugfix"
    repo.mkdir()
    init_git_repo(repo)

    calc_file = repo / "calculator.py"
    calc_file.write_text(
        "def multiply(a: int, b: int) -> int:\n    return a + b\n",
        encoding="utf-8",
    )

    test_file = repo / "test_calculator.py"
    test_file.write_text(
        "from calculator import multiply\n\ndef test_multiply():\n    assert multiply(3, 4) == 12\n",
        encoding="utf-8",
    )

    subprocess.run(["git", "add", "."], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-m", "initial buggy commit"], cwd=str(repo), check=True)

    # Verify initial failure
    res_init = subprocess.run(
        ["python", "-m", "pytest", "test_calculator.py"],
        cwd=str(repo),
        capture_output=True,
    )
    assert res_init.returncode != 0

    # --------------------------------------------------------------------------
    # Sequence of 11 Mock Decisions for the 4-layer orchestration:
    # --------------------------------------------------------------------------
    # 1. Architect Tick 1 -> Dispatch Scout
    arch_tick_1 = ArchitectDecision(
        situation_assessment="Initial test_multiply is failing. Dispatching SCOUT to locate bug.",
        mental_simulation=MentalSimulation(
            hypothesis="Need to scout calculator.py to diagnose the failure in multiply()",
            predicted_side_effects=[],
            verification_strategy="Read calculator.py",
        ),
        new_phase=TaskPhase.EXPLORING,
        next_action=ArchitectAction.DISPATCH_SCOUT,
        task_contract=TaskContract(
            contract_id="c_scout_1",
            target_role="SCOUT",
            objective="Inspect calculator.py",
            target_files=["calculator.py"],
            context_briefing="test_multiply is failing",
        ),
    )

    # 2. Scout Worker Step 1 -> read_file
    scout_step_1 = ChassisStepDecision(
        thought="Read calculator.py to find implementation of multiply",
        action_type="CALL_TOOL",
        tool_call=ToolCallSpec(
            tool_name="read_file",
            arguments={"path": "calculator.py"},
        ),
    )

    # 3. Scout Worker Step 2 -> finish
    scout_step_2 = ChassisStepDecision(
        thought="Found bug: multiply returns a + b instead of a * b",
        action_type="FINISH_TASK",
        final_report=FinalReportDraft(
            status=WorkerStatus.SUCCESS,
            summary="Located bug in multiply()",
            discovered_facts=[
                VerifiedFactInput(
                    statement="multiply returns a + b instead of a * b",
                    evidence_file="calculator.py",
                    start_line=1,
                    end_line=2,
                )
            ],
        ),
    )

    # 4. Architect Tick 2 -> Dispatch Surgeon
    arch_tick_2 = ArchitectDecision(
        situation_assessment="SCOUT discovered multiply() returns a + b instead of a * b.",
        mental_simulation=MentalSimulation(
            hypothesis="Patch multiply to return a * b",
            predicted_side_effects=["Fixes test_multiply"],
            verification_strategy="Run test_calculator.py",
        ),
        new_phase=TaskPhase.EXECUTING,
        next_action=ArchitectAction.DISPATCH_SURGEON,
        task_contract=TaskContract(
            contract_id="c_surg_1",
            target_role="SURGEON",
            objective="Fix multiply in calculator.py",
            target_files=["calculator.py"],
            context_briefing="Change return a + b to return a * b",
            test_command="python -m pytest test_calculator.py",
        ),
    )

    # 5. Surgeon Worker Step 1 -> apply_patch
    surgeon_step_1 = ChassisStepDecision(
        thought="Patching calculator.py with multiplication operator",
        action_type="CALL_TOOL",
        tool_call=ToolCallSpec(
            tool_name="apply_patch",
            arguments={
                "path": "calculator.py",
                "patches": [{"search": "return a + b", "replace": "return a * b"}],
            },
        ),
    )

    # 6. Surgeon Worker Step 2 -> run_command (test)
    surgeon_step_2 = ChassisStepDecision(
        thought="Running pytest to verify the fix",
        action_type="CALL_TOOL",
        tool_call=ToolCallSpec(
            tool_name="run_command",
            arguments={"command": "python -m pytest test_calculator.py"},
        ),
    )

    # 7. Surgeon Worker Step 3 -> finish
    surgeon_step_3 = ChassisStepDecision(
        thought="Tests pass, reporting surgery complete",
        action_type="FINISH_TASK",
        final_report=FinalReportDraft(
            status=WorkerStatus.SUCCESS,
            summary="Patched calculator.py and confirmed test passes",
            discovered_facts=[],
        ),
    )

    # 8. Architect Tick 3 -> Dispatch Inquisitor
    arch_tick_3 = ArchitectDecision(
        situation_assessment="SURGEON patched calculator.py. Independent verification needed.",
        mental_simulation=MentalSimulation(
            hypothesis="Verify patch independently with Inquisitor",
            predicted_side_effects=[],
            verification_strategy="Run pytest and inspect git diff",
        ),
        new_phase=TaskPhase.VERIFYING,
        next_action=ArchitectAction.DISPATCH_INQUISITOR,
        task_contract=TaskContract(
            contract_id="c_inq_1",
            target_role="INQUISITOR",
            objective="Verify calculator.py fix against acceptance criteria",
            target_files=["calculator.py"],
            context_briefing="Verify tests pass and zero regressions",
            test_command="python -m pytest test_calculator.py",
            acceptance_criteria=["multiply(3, 4) == 12"],
        ),
    )

    # 9. Inquisitor Worker Step 1 -> run_command
    inq_step_1 = ChassisStepDecision(
        thought="Executing verification test command",
        action_type="CALL_TOOL",
        tool_call=ToolCallSpec(
            tool_name="run_command",
            arguments={"command": "python -m pytest test_calculator.py"},
        ),
    )

    # 10. Inquisitor Worker Step 2 -> finish (APPROVED)
    inq_step_2 = ChassisStepDecision(
        thought="All criteria met and tests pass, issuing approval",
        action_type="FINISH_TASK",
        final_report=FinalReportDraft(
            status=WorkerStatus.SUCCESS,
            summary="All tests pass cleanly",
            inquisitor_verdict=InquisitorVerdict.APPROVED,
            discovered_facts=[],
        ),
    )

    # 11. Architect Tick 4 -> Complete Task
    arch_tick_4 = ArchitectDecision(
        situation_assessment="Inquisitor APPROVED the fix. Ready to complete.",
        mental_simulation=MentalSimulation(
            hypothesis="All verification completed",
            predicted_side_effects=[],
            verification_strategy="None",
        ),
        new_phase=TaskPhase.COMPLETED,
        next_action=ArchitectAction.COMPLETE_TASK,
        final_response="Bug in calculator.py successfully resolved and verified.",
    )

    mock_gateway = create_mock_gateway(
        [
            arch_tick_1,
            scout_step_1,
            scout_step_2,
            arch_tick_2,
            surgeon_step_1,
            surgeon_step_2,
            surgeon_step_3,
            arch_tick_3,
            inq_step_1,
            inq_step_2,
            arch_tick_4,
        ]
    )

    # Execute full AgentOS stack
    os_engine = AgentOS(workspace_root=repo, gateway=mock_gateway)
    outcome: TaskOutcome = await os_engine.execute_task(
        task_goal="Fix the bug in calculator.py so pytest passes."
    )

    assert outcome.success is True
    assert outcome.final_phase == TaskPhase.COMPLETED
    assert "successfully resolved" in outcome.summary

    # Verify physical file state on disk
    updated_code = calc_file.read_text(encoding="utf-8")
    assert "return a * b" in updated_code

    # Verify pytest passes directly
    res_final = subprocess.run(
        ["python", "-m", "pytest", "test_calculator.py"],
        cwd=str(repo),
        capture_output=True,
    )
    assert res_final.returncode == 0


# ==============================================================================
# Scenario 6: Live DeepSeek End-to-End Ephemeral Worker Execution
# ==============================================================================
@pytest.mark.asyncio
async def test_live_deepseek_ephemeral_worker_execution(tmp_path: Path) -> None:
    """Scenario 6: Live execution of SCOUT via EphemeralSquadDispatcher using DeepSeek API."""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        pytest.skip("DEEPSEEK_API_KEY is not set. Skipping live DeepSeek worker test.")

    repo = tmp_path / "repo_live_worker"
    repo.mkdir()
    init_git_repo(repo)

    # Create a fixture module to scout
    fixture_file = repo / "greeter.py"
    fixture_file.write_text(
        '"""Greeting utilities module."""\n\n'
        "def greet(name: str) -> str:\n"
        '    """Return personalized greeting."""\n'
        '    return f"Hello, {name}!"\n',
        encoding="utf-8",
    )

    config = KernelConfig(
        workspace_root=repo,
        deepseek_api_key=api_key,
    )
    gateway = ModelGateway(config=config)
    dispatcher = EphemeralSquadDispatcher(workspace_root=repo, gateway=gateway)

    contract = TaskContract(
        contract_id="contract_live_scout_001",
        target_role="SCOUT",
        objective="Read greeter.py and discover the greet function signature and line numbers.",
        target_files=["greeter.py"],
        context_briefing="Reconnaissance of greeter.py",
        acceptance_criteria=["Locate greet function"],
    )

    ctx = ExecutionContext(
        task_id="live_scout_task",
        role=AgentRole.SCOUT,
        workspace_root=repo,
    )

    report = await dispatcher.dispatch(contract=contract, ctx=ctx)

    assert isinstance(report, WorkerReport)
    assert report.worker_role == "SCOUT"
    assert report.status in (WorkerStatus.SUCCESS, WorkerStatus.FAILED)
    # SCOUT must never modify files
    assert report.modified_files == []


# ==============================================================================
# Scenario 7: Chassis Tool Signature Alignment & Graceful Defaults
# ==============================================================================
def test_chassis_tool_signature_alignment(tmp_path: Path) -> None:
    """Scenario 7: Verify every tool in SURGEON whitelist executes cleanly with prompt-advertised arguments."""
    workspace = tmp_path / "workspace_alignment"
    workspace.mkdir()
    code_file = workspace / "code.py"
    code_file.write_text("a = 1\nb = 2\n", encoding="utf-8")

    ctx = ExecutionContext(
        task_id="t_align",
        role=AgentRole.SURGEON,
        workspace_root=workspace,
        allowed_files=frozenset({"code.py", "new_file.py"}),
    )

    contract = TaskContract(
        contract_id="c_align",
        target_role="SURGEON",
        objective="Verify signatures",
        target_files=["code.py", "new_file.py"],
        context_briefing="Signature audit",
    )
    context_pkg = ContextPackage(contract=contract)
    gateway = create_mock_gateway([])
    registry = ToolRegistry()

    chassis = StandardAgentChassis(
        role_card=SURGEON_ROLE_CARD,
        context_pkg=context_pkg,
        ctx=ctx,
        gateway=gateway,
        tool_registry=registry,
    )

    # 1. read_file with advertised parameters and None defaults
    res_read = chassis._on_tool_call(
        ToolCallSpec(
            tool_name="read_file",
            arguments={"path": "code.py", "start_line": 1, "end_line": 10},
        )
    )
    assert res_read.success
    assert "a = 1" in res_read.output

    res_read_none = chassis._on_tool_call(
        ToolCallSpec(
            tool_name="read_file",
            arguments={"path": "code.py", "start_line": None, "end_line": None},
        )
    )
    assert res_read_none.success
    assert "a = 1" in res_read_none.output

    # 2. list_dir with advertised parameters and recursive alias
    res_list = chassis._on_tool_call(
        ToolCallSpec(
            tool_name="list_dir",
            arguments={"path": ".", "max_depth": 2},
        )
    )
    assert res_list.success
    assert "code.py" in res_list.output

    res_list_rec = chassis._on_tool_call(
        ToolCallSpec(
            tool_name="list_dir",
            arguments={"path": ".", "recursive": False},
        )
    )
    assert res_list_rec.success

    # 3. grep_search with advertised query/path/is_regex/max_results and pattern alias
    res_grep = chassis._on_tool_call(
        ToolCallSpec(
            tool_name="grep_search",
            arguments={"query": "a = 1", "path": ".", "is_regex": False, "max_results": 50},
        )
    )
    assert res_grep.success
    assert "code.py" in res_grep.output

    res_grep_alias = chassis._on_tool_call(
        ToolCallSpec(
            tool_name="grep_search",
            arguments={"pattern": "b = 2", "path": ".", "case_sensitive": True},
        )
    )
    assert res_grep_alias.success
    assert "code.py" in res_grep_alias.output

    # 4. find_files with advertised pattern/path/max_results
    res_find = chassis._on_tool_call(
        ToolCallSpec(
            tool_name="find_files",
            arguments={"pattern": "*.py", "path": ".", "max_results": 100},
        )
    )
    assert res_find.success
    assert "code.py" in res_find.output

    # 5. apply_patch with advertised path/patches/create_if_missing
    res_patch = chassis._on_tool_call(
        ToolCallSpec(
            tool_name="apply_patch",
            arguments={
                "path": "code.py",
                "patches": [{"search": "a = 1", "replace": "a = 100"}],
                "create_if_missing": False,
            },
        )
    )
    assert res_patch.success
    assert "a = 100" in code_file.read_text(encoding="utf-8")

    res_patch_create = chassis._on_tool_call(
        ToolCallSpec(
            tool_name="apply_patch",
            arguments={
                "path": "new_file.py",
                "patches": [{"search": "", "replace": "c = 3\n"}],
                "create_if_missing": True,
            },
        )
    )
    assert res_patch_create.success
    assert (workspace / "new_file.py").exists()

    # 6. run_command with advertised command/timeout_sec
    res_cmd = chassis._on_tool_call(
        ToolCallSpec(
            tool_name="run_command",
            arguments={
                "command": "python -c \"print('signature_aligned_successfully')\"",
                "timeout_sec": 10,
            },
        )
    )
    assert res_cmd.success
    assert "signature_aligned_successfully" in res_cmd.output

