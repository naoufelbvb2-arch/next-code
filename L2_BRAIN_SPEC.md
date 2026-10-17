# Engineering Specification: Layer 2 (L2) — Live Codebase Engine & Memory (`brain`)
**Project:** Micro-Engineering Agent OS  
**Phase:** Layer 2 (L2) Structural World Model, Working Memory & Procedural Skills  
**Python Version:** 3.11+  
**Core Validation Library:** Pydantic v2  
**Dependencies:** Layer 1 (`kernel`)

---

## 1. Architectural Philosophy & Invariants
Layer 2 (`brain`) serves as the **structural world model and cognitive memory substrate** of the Agent OS. Instead of forcing LLMs to repeatedly read full files to understand the project or maintain fragile conversation histories, Layer 2 builds a deterministic structural graph of the codebase, maintains a crash-resilient blackboard state (including a mandatory graveyard of failed hypotheses), and manages reusable procedural playbooks.

### Strict Invariants (Non-Negotiable Rules)
1. **Zero-LLM World Modeling:** The codebase graph (`RepoGraph`) must be constructed 100% deterministically using Python's `ast` module (for `.py` files) and fast regex/heuristic extractors (for `.js`, `.ts`, `.json`, `.md`, etc.) with zero LLM token consumption.
2. **Incremental Caching (`mtime` + `SHA256`):** `RepoGraph` must cache parsed file nodes and only re-parse files whose modification timestamp (`mtime_ns`) or SHA256 content hash has changed since the last scan.
3. **Self-Protected Internal Storage (`.agent_os/`):** All Layer 2 persistent states, caches, and custom playbooks reside inside `<workspace_root>/.agent_os/`. Therefore, `.agent_os` and `.agent_os/**` MUST be added to `DEFAULT_BLACKLIST_PATTERNS` in `kernel/config.py` so no agent tool (`read_file`, `apply_patch`, `list_dir`, `grep_search`) can ever tamper with or pollute context with internal OS state files.
4. **Anti-Amnesia Hypothesis Graveyard:** Whenever a task rolls back to a checkpoint or an execution attempt fails verification, `Blackboard` must record a `FailedHypothesis` entry. Every compiled context digest must prominently include these failed hypotheses so the Lead Architect (L3) never repeats a failed approach.
5. **Atomic State Persistence:** Every mutation to `Blackboard` or `PlaybookStore` on disk must use atomic write-replace (`tempfile.mkstemp` + `os.replace`) inside `.agent_os/` to guarantee zero corruption on crash or power loss.

---

## 2. Directory Structure & L1 Security Update

### 2.1 Mandatory Update to Layer 1 (`kernel/config.py` & `.gitignore`)
* Add `".agent_os"` and `".agent_os/**"` to `DEFAULT_BLACKLIST_PATTERNS` in `kernel/config.py`.
* Ensure `.agent_os/` is ignored during `GitShadowCheckpointer` snapshots (via `.git/info/exclude` or git plumbing exclusion) so rollbacks never erase the agent's memory or blackboard state! *(Critical Invariant: Rolling back code in the workspace must NOT roll back the Blackboard's record of why the rollback happened).*

### 2.2 Layer 2 File Tree
```text
agent_os/
├── L1_KERNEL_SPEC.md
├── L2_BRAIN_SPEC.md
├── kernel/                        # Layer 1 (Hardened + .agent_os exclusion)
├── brain/                         # Layer 2 (L2)
│   ├── __init__.py
│   ├── schemas.py                 # All Pydantic v2 data contracts for L2
│   ├── repo_graph.py              # Incremental AST/Dependency Graph & Impact Radius
│   ├── blackboard.py              # Persistent Working Memory, Hypothesis Graveyard & Digest Compiler
│   └── playbooks.py               # Procedural Skill Bank, Built-in SOPs & Trigger Matcher
└── tests/
    ├── test_l1_kernel.py
    └── test_l2_brain.py           # Comprehensive verification suite for Layer 2
```

---

## 3. Core Data Contracts (`brain/schemas.py`)

All models must use Pydantic v2 with `ConfigDict(extra="forbid")`.

### 3.1 Structural World Model Contracts (`RepoGraph`)
* **`SymbolKind(str, Enum)`:**
  * `CLASS = "CLASS"`
  * `FUNCTION = "FUNCTION"`
  * `ASYNC_FUNCTION = "ASYNC_FUNCTION"`
  * `METHOD = "METHOD"`
  * `GLOBAL_VAR = "GLOBAL_VAR"`
* **`SymbolNode(BaseModel)`:**
  * `name: str`
  * `qualified_name: str` (e.g., `"WorkspaceGuard.authorize_write"`)
  * `kind: SymbolKind`
  * `start_line: int`
  * `end_line: int`
  * `signature: str` (e.g., `"def authorize_write(self, ctx: ExecutionContext, target_path: str | Path) -> Path"`)
  * `docstring_summary: str | None = None` (First line of docstring if present)
* **`FileNode(BaseModel)`:**
  * `rel_path: str` (Normalized POSIX path relative to `workspace_root`)
  * `language: str` (`"python"`, `"json"`, `"markdown"`, `"typescript"`, `"other"`)
  * `line_count: int`
  * `sha256: str`
  * `mtime_ns: int`
  * `imports: list[str] = Field(default_factory=list)` (Normalized module or relative file references)
  * `symbols: list[SymbolNode] = Field(default_factory=list)`
* **`ImpactReport(BaseModel)`:**
  * `target_file: str`
  * `direct_dependents: list[str]` (Files that directly import `target_file`)
  * `transitive_dependents: list[str]` (Files affected up to 2 hops away)
  * `dependencies: list[str]` (Workspace files that `target_file` imports)

### 3.2 Working Memory & Blackboard Contracts (`Blackboard`)
* **`TaskPhase(str, Enum)`:**
  * `INITIALIZING = "INITIALIZING"`
  * `EXPLORING = "EXPLORING"`
  * `PLANNING = "PLANNING"`
  * `EXECUTING = "EXECUTING"`
  * `VERIFYING = "VERIFYING"`
  * `COMPLETED = "COMPLETED"`
  * `FAILED = "FAILED"`
* **`StepStatus(str, Enum)`:**
  * `PENDING = "PENDING"`
  * `IN_PROGRESS = "IN_PROGRESS"`
  * `PASSED = "PASSED"`
  * `FAILED = "FAILED"`
  * `SKIPPED = "SKIPPED"`
* **`VerifiedFact(BaseModel)`:**
  * `fact_id: str`
  * `statement: str`
  * `evidence_file: str`
  * `start_line: int`
  * `end_line: int`
  * `discovered_by: str = "SCOUT"`
* **`PlanStep(BaseModel)`:**
  * `step_id: str`
  * `description: str`
  * `assigned_role: Literal["SCOUT", "SURGEON", "INQUISITOR"]`
  * `target_files: list[str] = Field(default_factory=list)`
  * `status: StepStatus = StepStatus.PENDING`
  * `result_summary: str | None = None`
* **`FailedHypothesis(BaseModel)`:**
  * `attempt_number: int`
  * `hypothesis: str` (What the agent tried to do and why)
  * `modified_files: list[str]`
  * `failure_reason: str` (Exact test error, syntax error, or reviewer rejection reason)
  * `rolled_back_to_checkpoint: str | None = None`
  * `timestamp_iso: str`
* **`BlackboardState(BaseModel)`:**
  * `task_id: str`
  * `task_goal: str`
  * `phase: TaskPhase = TaskPhase.INITIALIZING`
  * `active_checkpoint_hash: str | None = None`
  * `checkpoint_history: list[str] = Field(default_factory=list)`
  * `verified_facts: list[VerifiedFact] = Field(default_factory=list)`
  * `plan_steps: list[PlanStep] = Field(default_factory=list)`
  * `failed_hypotheses: list[FailedHypothesis] = Field(default_factory=list)`
  * `notes: list[str] = Field(default_factory=list)`

### 3.3 Procedural Skill Contracts (`Playbooks`)
* **`Playbook(BaseModel)`:**
  * `playbook_id: str`
  * `title: str`
  * `trigger_keywords: list[str]` (Lowercase keywords/tags such as `["bugfix", "traceback", "pytest", "refactor", "api"]`)
  * `applicable_phases: list[TaskPhase]`
  * `sop_steps: list[str]` (Ordered Standard Operating Procedure instructions)
  * `verification_rules: list[str]` (Mandatory checks before declaring success)
  * `anti_patterns: list[str]` (Common pitfalls to avoid)

---

## 4. Component Specifications

### 4.1 Critical L1 Integration Fix (`kernel/checkpoint.py` & `kernel/config.py`)
1. In `kernel/config.py`: Add `".agent_os"` and `".agent_os/**"` to `DEFAULT_BLACKLIST_PATTERNS`.
2. In `kernel/checkpoint.py`:
   * Ensure `.agent_os` is never staged or deleted during `create_checkpoint()`, `get_diff()`, or `rollback()`.
   * Specifically:
     * During `create_checkpoint()` and `get_diff()`, after `git add -A` on the temporary index, run `git reset -- .agent_os` (with `check=False` in case `.agent_os` doesn't exist yet) on the temporary index before `git write-tree`, OR skip `.agent_os/` during `rollback()`'s newly-added file deletion loop so `.agent_os/` and all its contents remain 100% untouched across code rollbacks. (Implementing both index exclusion and rollback protection is required for defense-in-depth).

---

### 4.2 Structural World Model (`brain/repo_graph.py`)
Class: `RepoGraph`
* **Initialization:** `RepoGraph(workspace_root: Path, cache_file: Path | None = None)`
  * Default `cache_file` is `workspace_root / ".agent_os" / "cache" / "repo_graph.json"`.
  * Uses `WorkspaceGuard` from Layer 1 to skip any blacklisted directories/files automatically.
* **Core Methods:**
  1. `refresh(force: bool = False) -> dict[str, FileNode]`:
     * Walks `workspace_root` ignoring blacklisted paths.
     * For each file, compares `(mtime_ns, size)` and `sha256` against cached `FileNode`.
     * Only re-parses modified or new files; removes deleted files from graph.
     * Saves updated cache atomically to `cache_file`.
  2. **Python AST Extractor (`_parse_python_file`):**
     * Parses file via `ast.parse`.
     * Extracts top-level `ClassDef`, `FunctionDef`, `AsyncFunctionDef`, and inside classes extracts methods (`METHOD` with `qualified_name="ClassName.method_name"`).
     * Reconstructs clean function/method signatures including arguments, type annotations (`ast.unparse`), and return annotations (`-> ReturnType`) without the function body.
     * Records exact `lineno` (`start_line`) and `end_lineno` (`end_line`) and the first line of `ast.get_docstring(node)`.
     * Extracts `Import` and `ImportFrom` targets and resolves them to workspace-relative file paths where applicable (e.g., `from kernel.guard import WorkspaceGuard` resolves to `kernel/guard.py`).
  3. **Fallback Extractor:**
     * For non-Python text files (`.json`, `.md`, `.toml`, `.js`, `.ts`), records `line_count`, `sha256`, and basic regex symbol hints without failing if a file has syntax errors during mid-edit states (if `ast.parse` fails on a broken file, gracefully fallback to returning the `FileNode` with empty symbols rather than crashing `RepoGraph`).
  4. `get_impact_radius(rel_path: str) -> ImpactReport`:
     * Normalizes `rel_path` to POSIX.
     * Computes `dependencies` (workspace files imported by `rel_path`), `direct_dependents` (workspace files that import `rel_path`), and `transitive_dependents` (2nd-degree dependents).
  5. `get_file_skeleton(rel_path: str) -> str`:
     * Returns a compact, human-and-LLM-readable skeleton of a single file showing imports and every symbol with its `[start_line-end_line]`, `signature`, and `docstring_summary`.
  6. `render_repo_map(max_chars: int = 8000, focus_files: list[str] | None = None) -> str`:
     * Ranks files by centrality (number of inbound dependents + outbound dependencies, boosting `focus_files` to the top).
     * Renders a compact tree/skeleton view within `max_chars` budget so the Lead Architect gets maximum structural awareness per token.

---

### 4.3 Persistent Working Memory (`brain/blackboard.py`)
Class: `Blackboard`
* **Initialization:** `Blackboard(workspace_root: Path, task_id: str, task_goal: str = "")`
  * State file path: `workspace_root / ".agent_os" / "state" / f"{task_id}.json"`.
  * If state file already exists on disk, loads it automatically (enabling seamless crash recovery); otherwise initializes a new `BlackboardState` and persists it atomically.
* **Core Mutation Methods (All auto-persist atomically to disk):**
  1. `set_phase(phase: TaskPhase) -> None`
  2. `record_checkpoint(checkpoint_hash: str) -> None`: Sets `active_checkpoint_hash` and appends to `checkpoint_history`.
  3. `add_fact(statement: str, evidence_file: str, start_line: int, end_line: int, discovered_by: str = "SCOUT") -> VerifiedFact`: Avoids duplicate identical facts.
  4. `set_plan(steps: list[PlanStep]) -> None`
  5. `update_step_status(step_id: str, status: StepStatus, result_summary: str | None = None) -> None`
  6. `record_failed_hypothesis(hypothesis: str, modified_files: list[str], failure_reason: str, rolled_back_to_checkpoint: str | None = None) -> FailedHypothesis`:
     * Automatically increments `attempt_number` and records UTC ISO timestamp.
* **Context Compilation Method:**
  * `compile_context_digest(max_chars: int = 6000) -> str`:
    * Generates a structured Markdown digest tailored for the Lead Architect (L3) and Ephemeral Workers (L4).
    * **Priority Order in Digest:**
      1. Task ID, Goal, Phase, and Active Checkpoint Hash.
      2. **⚠️ GRAVEYARD OF FAILED HYPOTHESES (DO NOT REPEAT THESE):** Listed in full detail so the agent never repeats a broken approach.
      3. Current Plan Steps & Statuses.
      4. Verified Facts (with exact `file:start_line-end_line` citations).

---

### 4.4 Procedural Skill Bank (`brain/playbooks.py`)
Class: `PlaybookStore`
* **Initialization:** `PlaybookStore(workspace_root: Path)`
  * Storage directory: `workspace_root / ".agent_os" / "playbooks"`.
  * On initialization, automatically seeds **3 Built-in Core Engineering Playbooks** if the directory is empty:
    1. `pb_bugfix_tdd`: **"Root-Cause Bugfix & Regression Guard"** (Triggers: `bug`, `fix`, `error`, `exception`, `traceback`, `fail`, `broken`).
    2. `pb_feature_impl`: **"Clean Feature Implementation & Contract Alignment"** (Triggers: `implement`, `create`, `add`, `feature`, `build`, `module`).
    3. `pb_safe_refactor`: **"Impact-Aware Structural Refactoring"** (Triggers: `refactor`, `rename`, `clean`, `optimize`, `restructure`).
* **Core Methods:**
  1. `save_playbook(playbook: Playbook) -> None`: Persists playbook as JSON in `.agent_os/playbooks/{playbook_id}.json` atomically.
  2. `list_playbooks() -> list[Playbook]`
  3. `match_playbooks(task_description: str, phase: TaskPhase | None = None, top_k: int = 2) -> list[Playbook]`:
     * Scores playbooks deterministically based on keyword token overlap in `task_description` and optional `phase` filter, returning up to `top_k` relevant playbooks.
  4. `render_playbooks_prompt(playbooks: list[Playbook]) -> str`:
     * Formats matched playbooks into a crisp, directive Markdown block (`SOP Steps`, `Verification Rules`, `Anti-Patterns`) ready for prompt injection.

---

## 5. Verification & Acceptance Criteria (`tests/test_l2_brain.py`)

Layer 2 is considered complete **only** when both `tests/test_l1_kernel.py` and `tests/test_l2_brain.py` pass 100%, covering:

1. **Test 1 — `.agent_os` Security & Rollback Immunity:**
   * Verify `WorkspaceGuard` blocks `SCOUT`/`SURGEON` from reading or patching any file inside `.agent_os/`.
   * Verify that creating a checkpoint via `GitShadowCheckpointer`, writing new state/failed hypotheses to `Blackboard` (inside `.agent_os/`), modifying a workspace `.py` file, and calling `checkpointer.rollback(hash)` reverts the `.py` file **while preserving the `.agent_os/` Blackboard state 100% intact**.
2. **Test 2 — AST Parsing & Dependency Resolution (`RepoGraph`):**
   * On a multi-file workspace (or the project's own `kernel/` directory), `RepoGraph.refresh()` accurately extracts classes (`WorkspaceGuard`, `GitShadowCheckpointer`), methods with type annotations and line spans (`authorize_write`, `apply_patch`), and internal import edges (`kernel/tools/filesystem.py` -> `kernel/guard.py`, `kernel/schemas.py`).
3. **Test 3 — Incremental Caching Performance (`RepoGraph`):**
   * Verify that calling `refresh()` a second time without modifying files reuses the cached `FileNode` objects without re-parsing ASTs, and modifying a single file re-parses *only* that modified file.
4. **Test 4 — Impact Radius Calculation (`RepoGraph`):**
   * Calling `get_impact_radius("kernel/schemas.py")` or a fixture module accurately identifies direct and transitive dependent files.
5. **Test 5 — Blackboard Persistence, Recovery & Hypothesis Graveyard:**
   * Create a `Blackboard`, add verified facts, plan steps, and 2 `FailedHypothesis` records.
   * Instantiate a brand-new `Blackboard(workspace_root, task_id)` object from disk and verify 100% state recovery and verify `compile_context_digest()` highlights the Failed Hypotheses section.
6. **Test 6 — Playbook Seeding, Persistence & Matching (`PlaybookStore`):**
   * Verify the 3 built-in playbooks are automatically seeded on init, custom playbooks can be saved and reloaded, and `match_playbooks("Fix SyntaxError traceback in parser")` ranks `pb_bugfix_tdd` #1.