"""Verification & Acceptance Criteria test suite for Layer 2 (L2) - Live Codebase Engine & Memory.

Covers all 6 mandatory verification scenarios defined in Section 5 of L2_BRAIN_SPEC.md:
1. .agent_os Security & Rollback Immunity
2. AST Parsing & Dependency Resolution (RepoGraph)
3. Incremental Caching Performance (RepoGraph)
4. Impact Radius Calculation (RepoGraph)
5. Blackboard Persistence, Recovery & Hypothesis Graveyard
6. Playbook Seeding, Persistence & Matching (PlaybookStore)
"""

import os
import subprocess
import time
from pathlib import Path

import pytest

from brain.blackboard import Blackboard
from brain.playbooks import PlaybookStore
from brain.repo_graph import RepoGraph
from brain.schemas import (
    FileNode,
    ImpactReport,
    PlanStep,
    Playbook,
    StepStatus,
    SymbolKind,
    SymbolNode,
    TaskPhase,
)
from kernel.checkpoint import GitShadowCheckpointer
from kernel.exceptions import SecurityViolationError
from kernel.guard import WorkspaceGuard
from kernel.schemas import AgentRole, ExecutionContext, PatchBlock
from kernel.tools.filesystem import apply_patch, read_file


# ==============================================================================
# Scenario 1: .agent_os Security & Rollback Immunity
# ==============================================================================
def test_agent_os_security_and_rollback_immunity(tmp_path: Path) -> None:
    """Scenario 1: Verify .agent_os blacklist isolation and immunity from git plumbing rollbacks."""
    workspace = tmp_path / "repo"
    workspace.mkdir()

    # Initialize a test git repo
    subprocess.run(["git", "init"], cwd=str(workspace), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=str(workspace), check=True)
    subprocess.run(["git", "config", "user.email", "tester@test.com"], cwd=str(workspace), check=True)

    # Initial commit
    main_py = workspace / "main.py"
    main_py.write_text("def run():\n    return 'initial'\n", encoding="utf-8")
    subprocess.run(["git", "add", "main.py"], cwd=str(workspace), check=True)
    subprocess.run(["git", "commit", "-m", "init commit"], cwd=str(workspace), check=True)

    # 1. Verify WorkspaceGuard blocks reading or mutating .agent_os
    guard = WorkspaceGuard(workspace)
    agent_os_dir = workspace / ".agent_os"
    agent_os_dir.mkdir()
    secret_state = agent_os_dir / "secret.json"
    secret_state.write_text('{"key": "val"}', encoding="utf-8")

    with pytest.raises(SecurityViolationError):
        guard.resolve_safe_path(".agent_os/secret.json")

    ctx_scout = ExecutionContext(
        task_id="t1-scout",
        role=AgentRole.SCOUT,
        workspace_root=workspace,
    )
    res_read = read_file(ctx_scout, ".agent_os/secret.json")
    assert not res_read.success
    assert res_read.error_code == "PATH_VIOLATION"

    ctx_surgeon = ExecutionContext(
        task_id="t1-surgeon",
        role=AgentRole.SURGEON,
        workspace_root=workspace,
        allowed_files=frozenset({".agent_os/secret.json"}),
    )
    res_patch = apply_patch(
        ctx_surgeon,
        ".agent_os/secret.json",
        [PatchBlock(search='"val"', replace='"tampered"')],
    )
    assert not res_patch.success
    assert res_patch.error_code == "PATH_VIOLATION"

    # 2. Verify Rollback Immunity
    checkpointer = GitShadowCheckpointer(workspace)
    checkpoint_hash = checkpointer.create_checkpoint(label="before_blackboard")

    # Write persistent blackboard state inside .agent_os/
    bb = Blackboard(workspace, task_id="task_immunity_001", task_goal="Verify memory survives rollback")
    bb.set_phase(TaskPhase.EXECUTING)
    fh = bb.record_failed_hypothesis(
        hypothesis="Attempted flawed algorithm X",
        modified_files=["main.py"],
        failure_reason="Failed boundary test",
        rolled_back_to_checkpoint=checkpoint_hash,
    )

    # Verify state file exists on disk inside .agent_os/
    bb_state_file = workspace / ".agent_os" / "state" / "task_immunity_001.json"
    assert bb_state_file.exists()

    # Now mutate code in the workspace and add a new untracked file
    main_py.write_text("def run():\n    return 'corrupted_mutation'\n", encoding="utf-8")
    untracked_code = workspace / "temp_leak.py"
    untracked_code.write_text("leak = True\n", encoding="utf-8")

    # Perform code rollback
    checkpointer.rollback(checkpoint_hash)

    # 3. Assertions:
    # A. Workspace code is reverted
    assert main_py.read_text(encoding="utf-8") == "def run():\n    return 'initial'\n"
    assert not untracked_code.exists()

    # B. Memory inside .agent_os/ is 100% PRESERVED
    assert bb_state_file.exists()
    reloaded_bb = Blackboard(workspace, task_id="task_immunity_001")
    assert reloaded_bb.state.phase == TaskPhase.EXECUTING
    assert len(reloaded_bb.state.failed_hypotheses) == 1
    assert reloaded_bb.state.failed_hypotheses[0].hypothesis == "Attempted flawed algorithm X"
    assert reloaded_bb.state.failed_hypotheses[0].failure_reason == "Failed boundary test"


# ==============================================================================
# Scenario 2: AST Parsing & Dependency Resolution (RepoGraph)
# ==============================================================================
def test_ast_parsing_and_dependency_resolution(tmp_path: Path) -> None:
    """Scenario 2: Verify AST extraction of classes, methods, signatures, docstrings, and imports."""
    workspace = tmp_path / "repo_ast"
    workspace.mkdir()

    # Module A: Helper module
    mod_a = workspace / "helpers.py"
    mod_a.write_text(
        '"""Utility helpers for mathematical calculation."""\n'
        "\n"
        "GLOBAL_LIMIT = 1000\n"
        "\n"
        "def compute_delta(a: int, b: int = 0) -> int:\n"
        '    """Calculate delta between two numbers."""\n'
        "    return a - b\n",
        encoding="utf-8",
    )

    # Module B: Service class importing Module A
    pkg_dir = workspace / "services"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")

    mod_b = pkg_dir / "calculator.py"
    mod_b.write_text(
        "import helpers\n"
        "from helpers import compute_delta\n"
        "\n"
        "class Calculator:\n"
        '    """Scientific calculation engine."""\n'
        "\n"
        "    def __init__(self, precision: int = 2) -> None:\n"
        '        """Initialize calculator precision."""\n'
        "        self.precision = precision\n"
        "\n"
        "    async def calculate_async(self, val: int) -> float:\n"
        '        """Perform asynchronous calculation step."""\n'
        "        diff = compute_delta(val, 10)\n"
        "        return float(diff * self.precision)\n",
        encoding="utf-8",
    )

    graph = RepoGraph(workspace)
    nodes = graph.refresh(force=True)

    assert "helpers.py" in nodes
    assert "services/calculator.py" in nodes

    # Verify helpers.py AST symbols
    helpers_node = nodes["helpers.py"]
    symbols_map = {s.name: s for s in helpers_node.symbols}

    assert "compute_delta" in symbols_map
    fn_sym = symbols_map["compute_delta"]
    assert fn_sym.kind == SymbolKind.FUNCTION
    assert "compute_delta" in fn_sym.signature and "a: int" in fn_sym.signature and "-> int" in fn_sym.signature
    assert fn_sym.docstring_summary == "Calculate delta between two numbers."
    assert fn_sym.start_line == 5
    assert fn_sym.end_line == 7

    assert "GLOBAL_LIMIT" in symbols_map
    assert symbols_map["GLOBAL_LIMIT"].kind == SymbolKind.GLOBAL_VAR

    # Verify calculator.py AST symbols
    calc_node = nodes["services/calculator.py"]
    calc_symbols = {s.name: s for s in calc_node.symbols}

    assert "Calculator" in calc_symbols
    class_sym = calc_symbols["Calculator"]
    assert class_sym.kind == SymbolKind.CLASS
    assert "class Calculator:" in class_sym.signature

    assert "__init__" in calc_symbols
    init_sym = calc_symbols["__init__"]
    assert init_sym.kind == SymbolKind.METHOD
    assert init_sym.qualified_name == "Calculator.__init__"
    assert "__init__" in init_sym.signature and "precision: int" in init_sym.signature and "-> None" in init_sym.signature
    assert init_sym.docstring_summary == "Initialize calculator precision."

    assert "calculate_async" in calc_symbols
    async_sym = calc_symbols["calculate_async"]
    assert async_sym.kind == SymbolKind.METHOD
    assert async_sym.qualified_name == "Calculator.calculate_async"
    assert "async def calculate_async(self, val: int) -> float" in async_sym.signature

    # Verify internal dependency resolution: services/calculator.py imports helpers.py
    assert "helpers.py" in calc_node.imports


# ==============================================================================
# Scenario 3: Incremental Caching Performance (RepoGraph)
# ==============================================================================
def test_incremental_caching_performance(tmp_path: Path) -> None:
    """Scenario 3: Verify mtime/sha256 incremental caching and parse count behavior."""
    workspace = tmp_path / "cache_test"
    workspace.mkdir()

    file_a = workspace / "mod_a.py"
    file_b = workspace / "mod_b.py"
    file_c = workspace / "mod_c.py"

    file_a.write_text("x = 1\n", encoding="utf-8")
    file_b.write_text("y = 2\n", encoding="utf-8")
    file_c.write_text("z = 3\n", encoding="utf-8")

    cache_file = workspace / ".agent_os" / "cache" / "repo_graph.json"

    # Pass 1: Cold scan parses all 3 files
    graph1 = RepoGraph(workspace, cache_file=cache_file)
    graph1.refresh()
    assert graph1.parse_count == 3
    assert graph1.cache_hits == 0
    assert cache_file.exists()

    # Pass 2: Warm scan with no file changes reuses 100% of cached nodes
    graph2 = RepoGraph(workspace, cache_file=cache_file)
    graph2.refresh()
    assert graph2.parse_count == 0
    assert graph2.cache_hits == 3

    # Pass 3: Modify exactly one file (mod_b.py)
    # Ensure mtime change
    time.sleep(0.05)
    file_b.write_text("y = 200\n", encoding="utf-8")

    graph3 = RepoGraph(workspace, cache_file=cache_file)
    graph3.refresh()
    # Exactly 1 file parsed, 2 files hit cache
    assert graph3.parse_count == 1
    assert graph3.cache_hits == 2

    # Pass 4: Delete mod_c.py and ensure it is removed from graph
    file_c.unlink()
    graph4 = RepoGraph(workspace, cache_file=cache_file)
    nodes = graph4.refresh()
    assert "mod_c.py" not in nodes
    assert "mod_a.py" in nodes
    assert "mod_b.py" in nodes


# ==============================================================================
# Scenario 4: Impact Radius Calculation (RepoGraph)
# ==============================================================================
def test_impact_radius_calculation(tmp_path: Path) -> None:
    """Scenario 4: Verify direct and transitive blast radius calculation."""
    workspace = tmp_path / "impact_test"
    workspace.mkdir()

    # Graph structure:
    # leaf.py -> imported by mid_a.py and mid_b.py
    # mid_a.py -> imported by top.py
    # isolated.py -> standalone
    (workspace / "leaf.py").write_text("VAL = 42\n", encoding="utf-8")
    (workspace / "mid_a.py").write_text("import leaf\n", encoding="utf-8")
    (workspace / "mid_b.py").write_text("import leaf\n", encoding="utf-8")
    (workspace / "top.py").write_text("import mid_a\n", encoding="utf-8")
    (workspace / "isolated.py").write_text("x = 0\n", encoding="utf-8")

    graph = RepoGraph(workspace)
    graph.refresh(force=True)

    # 1. Impact radius of leaf.py
    impact_leaf = graph.get_impact_radius("leaf.py")
    assert impact_leaf.target_file == "leaf.py"
    assert impact_leaf.direct_dependents == ["mid_a.py", "mid_b.py"]
    assert impact_leaf.transitive_dependents == ["top.py"]
    assert impact_leaf.dependencies == []

    # 2. Impact radius of top.py
    impact_top = graph.get_impact_radius("top.py")
    assert impact_top.target_file == "top.py"
    assert impact_top.dependencies == ["mid_a.py"]
    assert impact_top.direct_dependents == []
    assert impact_top.transitive_dependents == []

    # 3. Skeleton & Repo Map checks
    skeleton = graph.get_file_skeleton("leaf.py")
    assert "File: leaf.py" in skeleton
    assert "VAL" in skeleton

    repo_map = graph.render_repo_map(max_chars=2000, focus_files=["top.py"])
    assert "Repository Structure Map" in repo_map
    assert "top.py" in repo_map
    assert "leaf.py" in repo_map


# ==============================================================================
# Scenario 5: Blackboard Persistence, Recovery & Hypothesis Graveyard
# ==============================================================================
def test_blackboard_persistence_recovery_and_graveyard(tmp_path: Path) -> None:
    """Scenario 5: Verify persistent memory, crash recovery, and hypothesis graveyard digest."""
    workspace = tmp_path / "bb_test"
    workspace.mkdir()
    task_id = "task_fix_parser_404"
    task_goal = "Resolve JSON decoding error in tool payload"

    # 1. Initialize Blackboard and record state
    bb = Blackboard(workspace, task_id=task_id, task_goal=task_goal)
    bb.set_phase(TaskPhase.PLANNING)
    bb.record_checkpoint("hash_init_001")

    # Add facts (including duplicate rejection check)
    f1 = bb.add_fact("Payload has trailing commas", "parser.py", 42, 45, discovered_by="SCOUT")
    f2 = bb.add_fact("Payload has trailing commas", "parser.py", 42, 45, discovered_by="SCOUT")
    assert f1.fact_id == f2.fact_id  # Duplicate prevented
    assert len(bb.state.verified_facts) == 1

    # Add plan steps
    step1 = PlanStep(
        step_id="step_1",
        description="Add regex stripper for trailing commas",
        assigned_role="SURGEON",
        target_files=["parser.py"],
    )
    bb.set_plan([step1])
    bb.update_step_status("step_1", StepStatus.PASSED, result_summary="Regex strip succeeded")

    # Record two failed hypotheses in the graveyard
    fh1 = bb.record_failed_hypothesis(
        hypothesis="Use strict json.loads without pre-cleaning",
        modified_files=["parser.py"],
        failure_reason="json.decoder.JSONDecodeError: Expecting property name enclosed in double quotes",
        rolled_back_to_checkpoint="hash_init_001",
    )
    assert fh1.attempt_number == 1

    fh2 = bb.record_failed_hypothesis(
        hypothesis="Replace standard json with ast.literal_eval",
        modified_files=["parser.py"],
        failure_reason="ValueError: malformed node or string on boolean true/false",
        rolled_back_to_checkpoint="hash_init_001",
    )
    assert fh2.attempt_number == 2

    bb.add_note("Ensure compatibility with UTF-8 byte order marks.")

    # 2. Simulate process restart by instantiating a new Blackboard instance from disk
    reloaded_bb = Blackboard(workspace, task_id=task_id)

    assert reloaded_bb.state.task_id == task_id
    assert reloaded_bb.state.task_goal == task_goal
    assert reloaded_bb.state.phase == TaskPhase.PLANNING
    assert reloaded_bb.state.active_checkpoint_hash == "hash_init_001"
    assert len(reloaded_bb.state.verified_facts) == 1
    assert len(reloaded_bb.state.plan_steps) == 1
    assert reloaded_bb.state.plan_steps[0].status == StepStatus.PASSED
    assert len(reloaded_bb.state.failed_hypotheses) == 2

    # 3. Context compilation digest verification
    digest = reloaded_bb.compile_context_digest(max_chars=4000)

    # Invariant: Graveyard of Failed Hypotheses must be prominent
    assert "⚠️ GRAVEYARD OF FAILED HYPOTHESES (DO NOT REPEAT THESE)" in digest
    assert "Attempt #1 [FAILED]" in digest
    assert "Use strict json.loads without pre-cleaning" in digest
    assert "json.decoder.JSONDecodeError" in digest
    assert "Attempt #2 [FAILED]" in digest
    assert "Replace standard json with ast.literal_eval" in digest

    # Verify Tactical Plan Steps and Verified Facts are rendered
    assert "Tactical Plan Steps" in digest
    assert "Add regex stripper for trailing commas" in digest
    assert "Verified Evidence & Facts" in digest
    assert "Payload has trailing commas" in digest
    assert "parser.py:42-45" in digest


# ==============================================================================
# Scenario 6: Playbook Seeding, Persistence & Matching (PlaybookStore)
# ==============================================================================
def test_playbook_seeding_persistence_and_matching(tmp_path: Path) -> None:
    """Scenario 6: Verify built-in SOP seeding, custom playbook saving, and keyword ranking."""
    workspace = tmp_path / "playbooks_test"
    workspace.mkdir()

    # 1. Initialize PlaybookStore and verify automatic seeding of 3 built-in playbooks
    store = PlaybookStore(workspace)
    playbooks = store.list_playbooks()
    pb_ids = {p.playbook_id for p in playbooks}

    assert "pb_bugfix_tdd" in pb_ids
    assert "pb_feature_impl" in pb_ids
    assert "pb_safe_refactor" in pb_ids

    # 2. Custom Playbook persistence
    custom_pb = Playbook(
        playbook_id="pb_db_migration",
        title="Zero-Downtime Database Migration",
        trigger_keywords=["migration", "schema", "database", "alembic", "sql"],
        applicable_phases=[TaskPhase.PLANNING, TaskPhase.EXECUTING],
        sop_steps=[
            "Draft backwards-compatible schema migration.",
            "Test migration up and down in isolation.",
        ],
        verification_rules=["Downgrade step leaves zero residue."],
        anti_patterns=["Do not execute destructive column drops in production."],
    )
    store.save_playbook(custom_pb)

    reloaded_pb = store.get_playbook("pb_db_migration")
    assert reloaded_pb is not None
    assert reloaded_pb.title == "Zero-Downtime Database Migration"

    # 3. Deterministic Playbook Matching
    # Query matching bugfix keywords should rank pb_bugfix_tdd #1
    matched_bug = store.match_playbooks("Fix SyntaxError traceback in parser", top_k=2)
    assert len(matched_bug) >= 1
    assert matched_bug[0].playbook_id == "pb_bugfix_tdd"

    # Query matching refactoring keywords
    matched_refactor = store.match_playbooks("Refactor and optimize module structure", top_k=2)
    assert len(matched_refactor) >= 1
    assert matched_refactor[0].playbook_id == "pb_safe_refactor"

    # Query with phase restriction
    matched_phase = store.match_playbooks(
        "Implement new feature component",
        phase=TaskPhase.PLANNING,
        top_k=2,
    )
    assert len(matched_phase) >= 1
    assert matched_phase[0].playbook_id == "pb_feature_impl"

    # 4. Directive Markdown Prompt Rendering
    prompt_output = store.render_playbooks_prompt(matched_bug)
    assert "# 📘 MANDATORY PROCEDURAL PLAYBOOKS" in prompt_output
    assert "[pb_bugfix_tdd] Root-Cause Bugfix & Regression Guard" in prompt_output
    assert "Standard Operating Procedure (SOP Steps):" in prompt_output
    assert "Mandatory Verification Rules:" in prompt_output
    assert "Forbidden Anti-Patterns:" in prompt_output
