"""Verification & Acceptance Criteria test suite for Layer 1 Deterministic Kernel.

Covers all 6 mandatory verification scenarios defined in Section 5 of L1_KERNEL_SPEC.md:
1. Path Escape & Symlink Jail
2. RBAC Enforcement
3. Syntax-Gated Patching
4. Shadow Checkpointing & Dirty Tree Rollback
5. Process Group Timeout & Truncation
6. Model Gateway & Structured Validation (including skippable live DeepSeek API test)
"""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from kernel.checkpoint import GitShadowCheckpointer
from kernel.config import KernelConfig
from kernel.exceptions import (
    BudgetExceededError,
    ModelOutputValidationError,
    PermissionDeniedError,
    SecurityViolationError,
)
from kernel.gateway.ledger import TokenLedger
from kernel.gateway.openai_compat import OpenAICompatibleProvider, ProviderConfig
from kernel.gateway.router import ModelGateway
from kernel.guard import WorkspaceGuard
from kernel.schemas import (
    AgentRole,
    ExecutionContext,
    Message,
    ModelProfile,
    PatchBlock,
)
from kernel.tools.filesystem import apply_patch, list_dir, read_file
from kernel.tools.registry import ToolRegistry
from kernel.tools.search import find_files, grep_search
from kernel.tools.shell import run_command


# ==============================================================================
# Helper Data Models for Structured Output Testing
# ==============================================================================
class CodeReviewResult(BaseModel):
    summary: str
    issues_count: int
    approved: bool


# ==============================================================================
# Scenario 1: Path Escape & Symlink Jail
# ==============================================================================
def test_path_escape_and_symlink_jail(tmp_path: Path) -> None:
    """Scenario 1: Verify zero path escape, symlink jail breach rejection, and blacklist enforcement."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_file = outside_dir / "secret.txt"
    outside_file.write_text("top_secret_data", encoding="utf-8")

    guard = WorkspaceGuard(workspace_root=workspace)

    # 1. Path escape via relative path traversal
    with pytest.raises(SecurityViolationError):
        guard.resolve_safe_path("../../etc/passwd")

    with pytest.raises(SecurityViolationError):
        guard.resolve_safe_path(workspace / ".." / "outside" / "secret.txt")

    # 2. Symlink / Junction jail breach rejection
    link_path = workspace / "escape_link"
    link_created = False
    try:
        os.symlink(outside_dir, link_path, target_is_directory=True)
        link_created = True
    except OSError:
        # On Windows without admin privileges, directory junctions can be created
        if sys.platform == "win32":
            try:
                import _winapi
                _winapi.CreateJunction(str(outside_dir), str(link_path))
                link_created = True
            except Exception:
                pass

    if link_created:
        with pytest.raises(SecurityViolationError):
            guard.resolve_safe_path("escape_link/secret.txt")

    # 3. Blacklist boundary enforcement (.env and .git/config)
    env_file = workspace / ".env"
    env_file.write_text("DB_PASSWORD=secret", encoding="utf-8")
    with pytest.raises(SecurityViolationError):
        guard.resolve_safe_path(".env")

    # Case-insensitive blacklist checks (e.g., Windows safety)
    with pytest.raises(SecurityViolationError):
        guard.resolve_safe_path(".ENV")

    env_local = workspace / ".env.production"
    env_local.write_text("API_KEY=prod", encoding="utf-8")
    with pytest.raises(SecurityViolationError):
        guard.resolve_safe_path(".env.production")

    git_dir = workspace / ".git"
    git_dir.mkdir()
    git_config = git_dir / "config"
    git_config.write_text("[core]\n\trepositoryformatversion = 0\n", encoding="utf-8")
    with pytest.raises(SecurityViolationError):
        guard.resolve_safe_path(".git/config")

    with pytest.raises(SecurityViolationError):
        guard.resolve_safe_path(".GIT/config")

    # 4. read_file tool wrapper integration check
    ctx = ExecutionContext(
        task_id="t1",
        role=AgentRole.ARCHITECT,
        workspace_root=workspace,
    )
    res_escape = read_file(ctx, "../../etc/passwd")
    assert not res_escape.success
    assert res_escape.error_code == "PATH_VIOLATION"

    res_env = read_file(ctx, ".env")
    assert not res_env.success
    assert res_env.error_code == "PATH_VIOLATION"

    res_env_case = read_file(ctx, ".ENV")
    assert not res_env_case.success
    assert res_env_case.error_code == "PATH_VIOLATION"


# ==============================================================================
# Scenario 2: RBAC Enforcement
# ==============================================================================
def test_rbac_enforcement(tmp_path: Path) -> None:
    """Scenario 2: Verify role-based access control and file scope restrictions."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    file_a = workspace / "file_a.py"
    file_b = workspace / "file_b.py"
    file_a.write_text("x = 1\n", encoding="utf-8")
    file_b.write_text("y = 2\n", encoding="utf-8")

    patch_a = [PatchBlock(search="x = 1", replace="x = 10")]
    patch_b = [PatchBlock(search="y = 2", replace="y = 20")]

    # 1. SCOUT attempting mutation returns PERMISSION_DENIED
    ctx_scout = ExecutionContext(
        task_id="t2-scout",
        role=AgentRole.SCOUT,
        workspace_root=workspace,
        allowed_files=frozenset({"file_a.py"}),
    )
    res_scout = apply_patch(ctx_scout, "file_a.py", patch_a)
    assert not res_scout.success
    assert res_scout.error_code == "PERMISSION_DENIED"

    # 2. INQUISITOR attempting mutation returns PERMISSION_DENIED
    ctx_inquisitor = ExecutionContext(
        task_id="t2-inq",
        role=AgentRole.INQUISITOR,
        workspace_root=workspace,
        allowed_files=frozenset({"file_a.py"}),
    )
    res_inq = apply_patch(ctx_inquisitor, "file_a.py", patch_a)
    assert not res_inq.success
    assert res_inq.error_code == "PERMISSION_DENIED"

    # 3. SURGEON attempting mutation outside allowed_files returns PERMISSION_DENIED
    ctx_surgeon = ExecutionContext(
        task_id="t2-surgeon",
        role=AgentRole.SURGEON,
        workspace_root=workspace,
        allowed_files=frozenset({"file_a.py"}),
    )
    res_surgeon_b = apply_patch(ctx_surgeon, "file_b.py", patch_b)
    assert not res_surgeon_b.success
    assert res_surgeon_b.error_code == "PERMISSION_DENIED"

    # 4. SURGEON on allowed file succeeds
    res_surgeon_a = apply_patch(ctx_surgeon, "file_a.py", patch_a)
    assert res_surgeon_a.success
    assert "x = 10" in file_a.read_text(encoding="utf-8")

    # 5. SCOUT attempting destructive shell command is blocked
    res_scout_cmd = run_command(ctx_scout, "rm file_a.py")
    assert not res_scout_cmd.success
    assert res_scout_cmd.error_code == "PERMISSION_DENIED"


# ==============================================================================
# Scenario 3: Syntax-Gated Patching
# ==============================================================================
def test_syntax_gated_patching(tmp_path: Path) -> None:
    """Scenario 3: Verify pre-write AST/JSON syntax verification and atomic writes."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    file_py = workspace / "file_a.py"
    initial_py_content = "def foo():\n    return 42\n"
    file_py.write_text(initial_py_content, encoding="utf-8")

    file_json = workspace / "config.json"
    initial_json_content = '{\n  "version": 1,\n  "enabled": true\n}\n'
    file_json.write_text(initial_json_content, encoding="utf-8")

    ctx = ExecutionContext(
        task_id="t3",
        role=AgentRole.SURGEON,
        workspace_root=workspace,
        allowed_files=frozenset({"file_a.py", "config.json"}),
    )

    # 1. Valid Python patch succeeds
    valid_py_patch = [PatchBlock(search="return 42", replace="return 100")]
    res_valid = apply_patch(ctx, "file_a.py", valid_py_patch)
    assert res_valid.success
    assert "return 100" in file_py.read_text(encoding="utf-8")

    # 2. Malformed Python patch (syntax error) is aborted and leaves file untouched
    py_snapshot_before = file_py.read_text(encoding="utf-8")
    invalid_py_patch = [PatchBlock(search="return 100", replace="def foo(:")]
    res_invalid_py = apply_patch(ctx, "file_a.py", invalid_py_patch)
    assert not res_invalid_py.success
    assert res_invalid_py.error_code == "SYNTAX_ERROR"
    # Ensure disk content is 100% unchanged
    assert file_py.read_text(encoding="utf-8") == py_snapshot_before

    # 3. Malformed JSON patch is aborted and leaves file untouched
    json_snapshot_before = file_json.read_text(encoding="utf-8")
    invalid_json_patch = [
        PatchBlock(search='"enabled": true', replace='"enabled": true, malformed_json')
    ]
    res_invalid_json = apply_patch(ctx, "config.json", invalid_json_patch)
    assert not res_invalid_json.success
    assert res_invalid_json.error_code == "SYNTAX_ERROR"
    # Ensure disk content is 100% unchanged
    assert file_json.read_text(encoding="utf-8") == json_snapshot_before

    # 4. Patch conflict: search block not found
    missing_patch = [PatchBlock(search="non_existent_code", replace="replacement")]
    res_missing = apply_patch(ctx, "file_a.py", missing_patch)
    assert not res_missing.success
    assert res_missing.error_code == "PATCH_CONFLICT"

    # 5. Patch conflict: search block ambiguous
    ambiguous_file = workspace / "ambiguous.py"
    ambiguous_file.write_text("val = 1\nval = 1\n", encoding="utf-8")
    ctx_amb = ExecutionContext(
        task_id="t3-amb",
        role=AgentRole.SURGEON,
        workspace_root=workspace,
        allowed_files=frozenset({"ambiguous.py", "file_a.py"}),
    )
    amb_patch = [PatchBlock(search="val = 1", replace="val = 2")]
    res_amb = apply_patch(ctx_amb, "ambiguous.py", amb_patch)
    assert not res_amb.success
    assert res_amb.error_code == "PATCH_CONFLICT"

    # 6. Patch conflict: empty search block on non-empty file
    empty_search_patch = [PatchBlock(search="", replace="prepend")]
    res_empty_search = apply_patch(ctx_amb, "file_a.py", empty_search_patch)
    assert not res_empty_search.success
    assert res_empty_search.error_code == "PATCH_CONFLICT"
    assert "Search block cannot be empty when patching a non-empty file." in res_empty_search.output


# ==============================================================================
# Scenario 4: Shadow Checkpointing & Dirty Tree Rollback
# ==============================================================================
def test_shadow_checkpointing_and_dirty_tree_rollback(tmp_path: Path) -> None:
    """Scenario 4: Verify low-level Git plumbing snapshot and dirty working tree rollback."""
    repo_dir = tmp_path / "git_repo"
    repo_dir.mkdir()

    # Initialize a test git repo
    subprocess.run(["git", "init"], cwd=str(repo_dir), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "TestUser"], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "config", "user.email", "test@kernel.os"], cwd=str(repo_dir), check=True)

    # Initial commit
    init_file = repo_dir / "init.txt"
    init_file.write_text("initial commit content\n", encoding="utf-8")
    subprocess.run(["git", "add", "init.txt"], cwd=str(repo_dir), check=True)
    subprocess.run(["git", "commit", "-m", "init commit"], cwd=str(repo_dir), check=True)

    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo_dir), check=True, capture_output=True, text=True
    ).stdout.strip()

    # Create dirty uncommitted changes and an untracked file
    init_file.write_text("dirty modified content\n", encoding="utf-8")
    untracked_file = repo_dir / "untracked.txt"
    untracked_file.write_text("untracked snapshot data\n", encoding="utf-8")

    status_before = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(repo_dir), check=True, capture_output=True, text=True
    ).stdout

    checkpointer = GitShadowCheckpointer(workspace_root=repo_dir)

    # 1. Create checkpoint: must not modify HEAD or working tree status
    checkpoint_hash = checkpointer.create_checkpoint(label="checkpoint_1")
    assert checkpoint_hash
    assert len(checkpoint_hash) >= 40

    head_after_checkpoint = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo_dir), check=True, capture_output=True, text=True
    ).stdout.strip()
    status_after_checkpoint = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(repo_dir), check=True, capture_output=True, text=True
    ).stdout

    assert head_before == head_after_checkpoint, "Checkpointer mutated HEAD!"
    assert status_before == status_after_checkpoint, "Checkpointer mutated git status!"

    # 2. Mutate working tree: modify tracked, delete untracked, add new file and file with spaces
    init_file.write_text("further modified post-checkpoint\n", encoding="utf-8")
    untracked_file.unlink()
    new_untracked_file = repo_dir / "new_created_file.txt"
    new_untracked_file.write_text("should be cleaned up\n", encoding="utf-8")
    new_spaced_file = repo_dir / "new file with spaces.txt"
    new_spaced_file.write_text("should also be cleaned up on rollback\n", encoding="utf-8")

    # 3. Test get_diff
    diff_output = checkpointer.get_diff(checkpoint_hash)
    assert "further modified post-checkpoint" in diff_output
    assert "new_created_file.txt" in diff_output
    assert "new file with spaces.txt" in diff_output

    # 4. Rollback to checkpoint
    checkpointer.rollback(checkpoint_hash)

    status_after_rollback = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(repo_dir), check=True, capture_output=True, text=True
    ).stdout

    # Verify complete restoration of tracked and untracked files
    assert status_after_rollback == status_before
    assert init_file.read_text(encoding="utf-8") == "dirty modified content\n"
    assert untracked_file.exists()
    assert untracked_file.read_text(encoding="utf-8") == "untracked snapshot data\n"
    assert not new_untracked_file.exists()
    assert not new_spaced_file.exists()


# ==============================================================================
# Scenario 5: Process Group Timeout & Smart Truncation
# ==============================================================================
def test_process_group_timeout_and_truncation(tmp_path: Path) -> None:
    """Scenario 5: Verify OS process tree termination on timeout and smart output truncation."""
    ctx = ExecutionContext(
        task_id="t5",
        role=AgentRole.ARCHITECT,
        workspace_root=tmp_path,
        command_timeout_sec=2,
    )

    # 1. Command spawning a sleeping child process with 2s timeout
    child_sleep_code = (
        "import subprocess, time, sys; "
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        "time.sleep(60)"
    )
    cmd = f'"{sys.executable}" -c "{child_sleep_code}"'

    t0 = time.perf_counter()
    res_timeout = run_command(ctx, cmd)
    duration = time.perf_counter() - t0

    assert not res_timeout.success
    assert res_timeout.error_code == "TIMEOUT"
    assert duration < 5.0, f"Process timeout took too long: {duration}s"

    # 2. Smart Output Truncation (50,000 characters)
    print_50k_code = (
        "import sys; "
        "sys.stdout.write('A' * 20000 + 'B' * 20000 + 'C' * 10000)"
    )
    trunc_cmd = f'"{sys.executable}" -c "{print_50k_code}"'

    res_trunc = run_command(ctx, trunc_cmd)
    assert res_trunc.success
    assert len(res_trunc.output) < 50000
    assert "... [TRUNCATED" in res_trunc.output
    assert res_trunc.output.startswith("A" * 100)
    assert res_trunc.output.endswith("C" * 100)


# ==============================================================================
# Scenario 6: Model Gateway & Structured Validation
# ==============================================================================
class MockLLMTransport(httpx.AsyncBaseTransport):
    """Mock HTTP transport returning controlled responses to test self-healing retry logic."""

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


@pytest.mark.asyncio
async def test_model_gateway_and_structured_validation(tmp_path: Path) -> None:
    """Scenario 6: Verify structured output validation, self-healing retries, and token accounting."""
    # Simulation responses: first returns broken JSON, second returns valid JSON conforming to CodeReviewResult
    mock_responses = [
        {
            "choices": [
                {
                    "message": {
                        "content": '{"summary": "incomplete review", "issues_count":'
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 15,
                "completion_tokens": 5,
                "total_tokens": 20,
            },
        },
        {
            "choices": [
                {
                    "message": {
                        "content": '{"summary": "Clean and secure code", "issues_count": 0, "approved": true}'
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 30,
                "completion_tokens": 12,
                "total_tokens": 42,
            },
        },
    ]

    mock_client = httpx.AsyncClient(transport=MockLLMTransport(mock_responses))
    provider = OpenAICompatibleProvider(
        config=ProviderConfig(api_key="mock_key"),
        client=mock_client,
    )

    ledger_log = tmp_path / "ledger.jsonl"
    ledger = TokenLedger(log_path=ledger_log)
    gateway = ModelGateway(
        config=KernelConfig(max_tokens_per_task=1000),
        ledger=ledger,
    )
    gateway.register_route(ModelProfile.FAST_WORKER, provider, model_name="deepseek-chat")

    task_id = "task-review-42"
    messages = [Message(role="user", content="Please review this diff.")]

    result, usage = await gateway.generate_structured(
        profile=ModelProfile.FAST_WORKER,
        messages=messages,
        response_model=CodeReviewResult,
        task_id=task_id,
        max_retries=2,
    )

    # Verify parsed structured result
    assert isinstance(result, CodeReviewResult)
    assert result.summary == "Clean and secure code"
    assert result.issues_count == 0
    assert result.approved is True

    # Verify cumulative token usage across attempts
    assert usage.prompt_tokens == 45  # 15 + 30
    assert usage.completion_tokens == 17  # 5 + 12
    assert usage.total_tokens == 62

    # Verify TokenLedger recorded exact usage and task summary
    summary = ledger.get_task_summary(task_id)
    assert summary["task_id"] == task_id
    assert summary["prompt_tokens"] == 45
    assert summary["completion_tokens"] == 17
    assert summary["total_tokens"] == 62

    # Verify JSONL persistence
    assert ledger_log.exists()
    logged_lines = ledger_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(logged_lines) == 1
    logged_entry = json.loads(logged_lines[0])
    assert logged_entry["total_tokens"] == 62

    # Verify budget cap enforcement
    with pytest.raises(BudgetExceededError):
        ledger.enforce_budget(task_id, max_tokens=50)

    # Verify exhaustion of retries raises ModelOutputValidationError
    always_broken_responses = [
        {
            "choices": [{"message": {"content": "broken json {{"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        }
    ]
    broken_client = httpx.AsyncClient(
        transport=MockLLMTransport(always_broken_responses)
    )
    broken_provider = OpenAICompatibleProvider(client=broken_client)
    gateway.register_route(ModelProfile.REASONING, broken_provider)

    with pytest.raises(ModelOutputValidationError):
        await gateway.generate_structured(
            profile=ModelProfile.REASONING,
            messages=messages,
            response_model=CodeReviewResult,
            task_id="task-fail",
            max_retries=1,
        )


@pytest.mark.asyncio
async def test_live_deepseek_api_integration() -> None:
    """Live integration test with DeepSeek API verifying text and structured generation."""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        pytest.skip("DEEPSEEK_API_KEY environment variable is not configured.")

    config = KernelConfig(deepseek_api_key=api_key)
    gateway = ModelGateway(config=config)

    # 1. Live unstructured text generation
    text_msgs = [Message(role="user", content="Respond with the exact word 'PONG' only.")]
    text_out, text_usage = await gateway.generate_text(
        profile=ModelProfile.FAST_WORKER,
        messages=text_msgs,
        task_id="live-text-task",
        max_tokens=50,
    )
    assert text_out.strip()
    assert text_usage.total_tokens > 0

    # 2. Live structured output generation
    struct_msgs = [
        Message(
            role="user",
            content="Return a JSON object with 'summary'='Live test passed', 'issues_count'=0, 'approved'=true.",
        )
    ]
    result, struct_usage = await gateway.generate_structured(
        profile=ModelProfile.FAST_WORKER,
        messages=struct_msgs,
        response_model=CodeReviewResult,
        task_id="live-structured-task",
    )
    assert isinstance(result, CodeReviewResult)
    assert result.approved is True
    assert struct_usage.total_tokens > 0


# ==============================================================================
# Additional Edge Cases & Tool Coverage Tests
# ==============================================================================
def test_filesystem_edge_cases(tmp_path: Path) -> None:
    """Verify filesystem line limits, non-existent files, and create_if_missing."""
    workspace = tmp_path / "fs_test"
    workspace.mkdir()
    sample_file = workspace / "sample.txt"
    lines = [f"line_{i}\n" for i in range(1, 350)]
    sample_file.write_text("".join(lines), encoding="utf-8")

    ctx = ExecutionContext(
        task_id="t-fs",
        role=AgentRole.SURGEON,
        workspace_root=workspace,
        allowed_files=frozenset({"sample.txt", "new_file.txt"}),
    )

    # 1. read_file with span > 300 rejected
    res_span = read_file(ctx, "sample.txt", start_line=1, end_line=305)
    assert not res_span.success
    assert res_span.error_code == "EXECUTION_ERROR"

    # 2. read_file with start_line < 1 rejected
    res_start = read_file(ctx, "sample.txt", start_line=0, end_line=10)
    assert not res_start.success
    assert res_start.error_code == "EXECUTION_ERROR"

    # 3. read_file valid slice
    res_valid = read_file(ctx, "sample.txt", start_line=10, end_line=20)
    assert res_valid.success
    assert "  10 | line_10" in res_valid.output
    assert res_valid.metadata["total_lines"] == 349

    # 4. list_dir
    res_list = list_dir(ctx, ".")
    assert res_list.success
    assert "sample.txt" in res_list.output

    # 5. apply_patch create_if_missing=True
    patch_new = [PatchBlock(search="", replace="hello world")]
    res_create = apply_patch(ctx, "new_file.txt", patch_new, create_if_missing=True)
    assert res_create.success
    assert (workspace / "new_file.txt").read_text(encoding="utf-8") == "hello world"


def test_search_tools(tmp_path: Path) -> None:
    """Verify grep_search and find_files behavior."""
    workspace = tmp_path / "search_test"
    workspace.mkdir()
    (workspace / "module_a.py").write_text("def calculate_total():\n    return 42\n", encoding="utf-8")
    (workspace / "module_b.py").write_text("def calculate_subtotal():\n    return 21\n", encoding="utf-8")

    ctx = ExecutionContext(
        task_id="t-search",
        role=AgentRole.SCOUT,
        workspace_root=workspace,
    )

    # 1. grep_search literal
    res_grep = grep_search(ctx, "calculate_")
    assert res_grep.success
    assert "calculate_total" in res_grep.output
    assert "calculate_subtotal" in res_grep.output

    # 2. find_files glob
    res_find = find_files(ctx, "*.py")
    assert res_find.success
    assert "module_a.py" in res_find.output
    assert "module_b.py" in res_find.output


def test_tool_registry(tmp_path: Path) -> None:
    """Verify ToolRegistry dispatch and execution."""
    workspace = tmp_path / "registry_test"
    workspace.mkdir()
    (workspace / "hello.txt").write_text("hello registry\n", encoding="utf-8")

    registry = ToolRegistry()
    assert "read_file" in registry.list_tools()
    assert "run_command" in registry.list_tools()

    ctx = ExecutionContext(
        task_id="t-reg",
        role=AgentRole.ARCHITECT,
        workspace_root=workspace,
    )

    # Valid execution
    res = registry.execute("read_file", ctx, path="hello.txt")
    assert res.success
    assert "hello registry" in res.output

    # Unknown tool
    res_unknown = registry.execute("non_existent_tool", ctx)
    assert not res_unknown.success
    assert res_unknown.error_code == "EXECUTION_ERROR"
