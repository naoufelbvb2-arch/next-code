# Engineering Specification: Layer 1 (L1) — The Deterministic Kernel
**Project:** Micro-Engineering Agent OS  
**Phase:** Layer 1 (L1) Foundation  
**Python Version:** 3.11+  
**Core Validation Library:** Pydantic v2  
**Default LLM Provider:** DeepSeek API (OpenAI-Compatible Architecture, Extensible to Local/Cloud Models)

---

## 1. Architectural Philosophy & Invariants
Layer 1 (`kernel`) is a **100% deterministic, zero-agency execution substrate**. It contains no autonomous decision loops or prompts. Its sole responsibility is to enforce security boundaries, guarantee atomic file/git recovery, execute sandboxed tools safely, and provide a unified, tracked gateway to LLM providers.

### Strict Invariants (Non-Negotiable Rules)
1. **Zero Path Escape:** No operation may read, write, or execute anything outside the resolved `workspace_root` or inside blacklisted paths (`.git/`, `.env*`, etc.). Symlinks pointing outside the root must be rejected immediately.
2. **Role & Scope Enforcement:** Every tool invocation requires an `ExecutionContext`. A `SCOUT` or `INQUISITOR` can never mutate files. A `SURGEON` can only mutate files explicitly listed in its `allowed_files` contract.
3. **Dirty-Tree Safe Checkpointing:** Checkpoints must capture both tracked and untracked files using low-level Git plumbing (`GIT_INDEX_FILE` + `git write-tree` + `git commit-tree`) without altering the user's `HEAD`, branches, or `git stash`.
4. **Syntax-Gated Mutations:** Any patch applied to a `.py` or `.json` file must undergo an in-memory syntax check (`ast.parse` / `json.loads`) *before* being written to disk. Invalid syntax rejects the patch immediately.
5. **Process Group Isolation:** All shell commands must run in a dedicated OS process group (`os.setsid` / `CREATE_NEW_PROCESS_GROUP`) with `stdin=DEVNULL`. Timeouts must terminate the entire process tree (`SIGKILL`), leaving zero zombie processes.

---

## 2. Directory Structure

```text
agent_os/
├── pyproject.toml
└── kernel/
    ├── __init__.py
    ├── config.py                  # KernelConfig & environment settings
    ├── exceptions.py              # Custom deterministic kernel exceptions
    ├── schemas.py                 # All Pydantic v2 data contracts for L1
    ├── guard.py                   # WorkspaceGuard (Path jail & RBAC)
    ├── checkpoint.py              # GitShadowCheckpointer (Git plumbing snapshots)
    ├── tools/
    │   ├── __init__.py
    │   ├── registry.py            # ToolRegistry & execution dispatcher
    │   ├── filesystem.py          # read_file, list_dir, apply_patch (AST/JSON gated)
    │   ├── search.py              # grep_search, find_files
    │   └── shell.py               # run_command (Process group + smart truncation)
    └── gateway/
        ├── __init__.py
        ├── base.py                # Abstract LLMProvider interface
        ├── openai_compat.py       # DeepSeek / OpenAI / vLLM / Ollama adapter
        ├── router.py              # ModelGateway (Profile-based routing & retries)
        └── ledger.py              # TokenLedger (Usage & cost tracking)
```

---

## 3. Core Data Contracts (`kernel/schemas.py`)

All inputs and outputs across L1 must strictly use these Pydantic v2 models (`ConfigDict(extra="forbid", frozen=True)` where applicable):

### 3.1 Security & Context Models
* **`AgentRole(str, Enum)`:**
  * `ARCHITECT = "ARCHITECT"`
  * `SCOUT = "SCOUT"`
  * `SURGEON = "SURGEON"`
  * `INQUISITOR = "INQUISITOR"`
* **`ExecutionContext(BaseModel)`:**
  * `task_id: str`
  * `role: AgentRole`
  * `workspace_root: Path`
  * `allowed_files: frozenset[str] = Field(default_factory=frozenset)` (Relative paths from `workspace_root` permitted for mutation by `SURGEON`).
  * `command_timeout_sec: int = Field(default=30, ge=1, le=300)`

### 3.2 Tool I/O Contracts
* **`ToolResult(BaseModel)`:**
  * `success: bool`
  * `output: str`
  * `error_code: str | None = None` (`PATH_VIOLATION`, `PERMISSION_DENIED`, `SYNTAX_ERROR`, `PATCH_CONFLICT`, `TIMEOUT`, `EXECUTION_ERROR`)
  * `execution_time_ms: float`
  * `metadata: dict[str, Any] = Field(default_factory=dict)`
* **`PatchBlock(BaseModel)`:**
  * `search: str` (Exact block of lines to find)
  * `replace: str` (Exact block of lines to replace with)

### 3.3 Model Gateway Contracts
* **`ModelProfile(str, Enum)`:**
  * `REASONING = "REASONING"` (Maps to `deepseek-reasoner` or `deepseek-chat` by default)
  * `FAST_WORKER = "FAST_WORKER"` (Maps to `deepseek-chat`)
  * `LOCAL_CODER = "LOCAL_CODER"` (Reserved for future local LLM integration)
* **`Message(BaseModel)`:**
  * `role: Literal["system", "user", "assistant", "tool"]`
  * `content: str`
  * `name: str | None = None`
  * `tool_call_id: str | None = None`
* **`TokenUsage(BaseModel)`:**
  * `prompt_tokens: int`
  * `completion_tokens: int`
  * `total_tokens: int`
  * `latency_ms: float`
  * `model_name: str`
  * `task_id: str`

---

## 4. Component Specifications

### 4.1 Workspace & Security Guard (`kernel/guard.py`)
Class: `WorkspaceGuard`
* **Initialization:** Accepts `workspace_root: Path` and `blacklist_patterns: list[str]` (Defaults: `[".git", ".git/**", ".env", ".env.*", "*.pem", "*.key", "__pycache__/**"]`).
* **Methods:**
  1. `resolve_safe_path(rel_or_abs_path: str | Path) -> Path`:
     * Resolves symlinks via `Path.resolve()`.
     * Verifies `resolved_path.is_relative_to(self.workspace_root.resolve())`.
     * Checks relative path against `blacklist_patterns` using `fnmatch` / `PurePath.match`.
     * Raises `SecurityViolationError` if any check fails.
  2. `authorize_read(ctx: ExecutionContext, target_path: str | Path) -> Path`:
     * Calls `resolve_safe_path(target_path)`. Allowed for all roles.
  3. `authorize_write(ctx: ExecutionContext, target_path: str | Path) -> Path`:
     * Calls `resolve_safe_path(target_path)`.
     * Verifies `ctx.role == AgentRole.SURGEON`.
     * Verifies the normalized relative path is in `ctx.allowed_files`.
     * Raises `PermissionDeniedError` if role is not `SURGEON` or file is not in `allowed_files`.
  4. `authorize_command(ctx: ExecutionContext, command: str) -> None`:
     * Blocks `SCOUT` from running destructive shell commands if restricted, or blocks hardcoded catastrophic patterns (`rm -rf /`, `git reset --hard`, `git clean -fd`, modifying `.git`).

---

### 4.2 Git Shadow Checkpointer (`kernel/checkpoint.py`)
Class: `GitShadowCheckpointer`
* **Purpose:** Create and restore complete workspace states (tracked + untracked non-ignored files) without touching the user's git index or commit history.
* **Mechanism:**
  1. `create_checkpoint(label: str = "") -> str`:
     * Creates a temporary file for `GIT_INDEX_FILE`.
     * Copies current `.git/index` to the temporary index file (if `.git/index` exists).
     * Runs `git add -A` with `env={"GIT_INDEX_FILE": temp_index_path}` inside `workspace_root`.
     * Runs `git write-tree` with the temporary index to get `tree_hash`.
     * Runs `git commit-tree <tree_hash> -m "shadow-checkpoint: <label>"` to get a dangling `commit_hash` (prevents garbage collection during session).
     * Optionally stores ref under `refs/agent_checkpoints/<commit_hash>`.
     * Deletes `temp_index_path` and returns `commit_hash`.
  2. `rollback(checkpoint_hash: str) -> None`:
     * Verifies `checkpoint_hash` exists via `git cat-file -t`.
     * Restores files from the checkpoint tree to the working directory (`git read-tree` + `git checkout-index -a -f` using a temporary index, then cleans untracked files created *after* the checkpoint by comparing trees).
  3. `get_diff(checkpoint_hash: str) -> str`:
     * Creates a temporary index of the current working state, writes tree `current_tree`, and runs `git diff-tree -p <checkpoint_hash> <current_tree>` to return a clean unified diff of all changes (including new untracked files).

---

### 4.3 Sandboxed Tool Runtime (`kernel/tools/`)

#### A. Filesystem Tools (`filesystem.py`)
1. **`read_file(ctx: ExecutionContext, path: str, start_line: int = 1, end_line: int = 250) -> ToolResult`**
   * Enforces maximum span of 300 lines per call (`end_line - start_line <= 300`).
   * Returns lines formatted as `"{line_num:4d} | {line_content}"` along with total line count in metadata.
2. **`list_dir(ctx: ExecutionContext, path: str = ".", max_depth: int = 2) -> ToolResult`**
   * Lists directory tree ignoring blacklisted patterns and `.gitignore` entries.
3. **`apply_patch(ctx: ExecutionContext, path: str, patches: list[PatchBlock], create_if_missing: bool = False) -> ToolResult`**
   * Calls `guard.authorize_write(ctx, path)`.
   * Reads original content (or starts with `""` if `create_if_missing=True` and file doesn't exist).
   * Sequentially applies each `PatchBlock`:
     * Normalizes line endings (`\r\n` to `\n`).
     * Counts occurrences of `block.search` in current content.
     * If `count == 0`: aborts entire operation and returns `error_code="PATCH_CONFLICT"` with detailed message.
     * If `count > 1`: aborts entire operation and returns `error_code="PATCH_CONFLICT"` ("Search block is ambiguous; matches multiple locations. Include more surrounding context lines.").
     * Replaces `block.search` with `block.replace`.
   * **Syntax Verification Gate (Pre-Write):**
     * If `path.endswith(".py")`: runs `ast.parse(new_content, filename=path)`. If `SyntaxError` is raised, aborts write and returns `error_code="SYNTAX_ERROR"` with exact line, offset, and traceback text.
     * If `path.endswith(".json")`: runs `json.loads(new_content)`. If `JSONDecodeError` is raised, aborts write and returns `error_code="SYNTAX_ERROR"`.
   * **Atomic Write:** Writes to a temporary file in the same directory and performs `os.replace()` to target file.

#### B. Search Tools (`search.py`)
1. **`grep_search(ctx: ExecutionContext, query: str, sub_path: str = ".", is_regex: bool = False, max_results: int = 50) -> ToolResult`**
   * Uses `rg` (ripgrep) if available on system PATH, with automatic fallback to pure Python regex search over non-blacklisted files.
   * Caps output at `max_results` matches and truncates lines longer than 300 characters.
2. **`find_files(ctx: ExecutionContext, pattern: str, sub_path: str = ".") -> ToolResult`**
   * Fast glob matching across workspace filenames.

#### C. Shell Execution (`shell.py`)
1. **`run_command(ctx: ExecutionContext, command: str, cwd_rel: str = ".") -> ToolResult`**
   * Calls `guard.authorize_command(ctx, command)`.
   * Executes via `subprocess.Popen` with:
     * `cwd = guard.resolve_safe_path(cwd_rel)`
     * `stdin = subprocess.DEVNULL`
     * `stdout = subprocess.PIPE`, `stderr = subprocess.PIPE`
     * `start_new_session = True` (POSIX process group isolation)
   * Waits up to `ctx.command_timeout_sec`.
   * On ` subprocess.TimeoutExpired`:
     * Sends `SIGKILL` to the entire process group (`os.killpg(os.getpgid(proc.pid), signal.SIGKILL)`).
     * Returns `success=False`, `error_code="TIMEOUT"`, plus partial output captured before timeout.
   * **Smart Output Truncation:**
     * Combines `stdout` and `stderr` clearly.
     * If total length exceeds `12,000` characters: keeps first `4,000` characters + `"\n\n... [TRUNCATED {N} CHARS] ...\n\n"` + last `6,000` characters.

---

### 4.4 Unified Model Gateway (`kernel/gateway/`)

#### A. Provider Abstraction (`base.py` & `openai_compat.py`)
* `BaseLLMProvider(ABC)` defines:
  * `async def generate_text(messages: list[Message], temperature: float, max_tokens: int) -> tuple[str, TokenUsage]`
  * `async def generate_structured(messages: list[Message], response_model: type[T], temperature: float) -> tuple[T, TokenUsage]`
* `OpenAICompatibleProvider(BaseLLMProvider)`:
  * Built using `httpx.AsyncClient` or `openai.AsyncOpenAI`.
  * Configured out-of-the-box for **DeepSeek**:
    * `base_url`: `os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")`
    * `api_key`: `os.getenv("DEEPSEEK_API_KEY")`
  * Supports future local providers (Ollama, vLLM, LM Studio) or OpenAI/Groq simply by passing a different `ProviderConfig(base_url, api_key, default_model)`.
  * **Structured Output Enforcement:**
    * Injects the JSON schema of `response_model.model_json_schema()` into the system prompt / `response_format={"type": "json_object"}`.
    * Parses response via `response_model.model_validate_json()`.
    * **Self-Healing Retry:** If JSON validation fails, automatically retries up to `max_retries=2` by feeding the `ValidationError` back to the model before raising `ModelOutputValidationError`.

#### B. Router & Token Ledger (`router.py` & `ledger.py`)
* `TokenLedger`: Thread-safe/Async-safe in-memory and JSONL logger that records every `TokenUsage` event per `task_id` and `ModelProfile`, providing `get_task_summary(task_id)` and enforcing budget caps (`max_tokens_per_task`).
* `ModelGateway`:
  * Maps `ModelProfile` (`REASONING`, `FAST_WORKER`, `LOCAL_CODER`) to registered provider instances.
  * Automatically records usage to `TokenLedger` on every call.
  * Handles transient HTTP errors (`429 Rate Limit`, `502`, `503`) with exponential backoff.

---

## 5. Verification & Acceptance Criteria (Test Suite Requirements)

The implementation of Layer 1 is considered complete **only** when an automated `pytest` suite (`tests/test_l1_kernel.py`) passes 100% of the following verification scenarios:

1. **Test 1 — Path Escape & Symlink Jail:**
   * Attempting to read `../../etc/passwd` or a symlink inside the workspace pointing outside the workspace raises `SecurityViolationError`.
   * Attempting to read `.env` or `.git/config` raises `SecurityViolationError`.
2. **Test 2 — RBAC Enforcement:**
   * `SCOUT` calling `apply_patch` returns `PERMISSION_DENIED`.
   * `SURGEON` calling `apply_patch` on `file_b.py` when `allowed_files={"file_a.py"}` returns `PERMISSION_DENIED`.
3. **Test 3 — Syntax-Gated Patching:**
   * `SURGEON` applying a valid Python patch to `file_a.py` succeeds.
   * `SURGEON` applying a Python patch with a missing parenthesis `def foo(:` returns `SYNTAX_ERROR` and leaves `file_a.py` untouched on disk.
   * Applying a malformed JSON patch to `config.json` returns `SYNTAX_ERROR` and leaves `config.json` untouched.
4. **Test 4 — Shadow Checkpointing & Dirty Tree Rollback:**
   * In a temporary git repo with an existing uncommitted change and an untracked file, `create_checkpoint()` creates a snapshot without changing `git log` or `git status`.
   * After modifying tracked files, deleting a file, and adding a new untracked file, calling `rollback(hash)` restores the exact pre-checkpoint state cleanly.
5. **Test 5 — Process Group Timeout & Truncation:**
   * Running a command that spawns a child process sleeping for 60s (`python -c "import subprocess, time; subprocess.Popen(['sleep', '60']); time.sleep(60)"`) with a 2s timeout terminates within ~2.5s, returns `error_code="TIMEOUT"`, and leaves no orphan `sleep` process running.
   * Running a command that prints 50,000 characters returns a truncated output containing both the head and tail markers.
6. **Test 6 — Model Gateway & Structured Validation:**
   * `ModelGateway.generate_structured()` with a Pydantic model parses valid JSON, retries automatically when given simulated broken JSON, and logs exact token counts in `TokenLedger`.
   * Includes a live integration test (skippable if `DEEPSEEK_API_KEY` is not set in environment) that verifies end-to-end communication with DeepSeek API.
