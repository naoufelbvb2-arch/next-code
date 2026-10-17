# Engineering Specification: Layer 4 (L4) — Standard Agent Template & Ephemeral Squads (`squad`)
**Project:** Micro-Engineering Agent OS  
**Phase:** Layer 4 (L4) Universal Ephemeral Chassis, Role Cards, Sliding Memory & Full OS Facade  
**Python Version:** 3.11+  
**Core Validation Library:** Pydantic v2  
**Dependencies:** Layer 1 (`kernel`), Layer 2 (`brain`), Layer 3 (`architect`)

---

## 1. Architectural Philosophy & Invariants
Layer 4 (`squad`) implements the **Universal Ephemeral Agent Pattern**. Instead of building separate, ad-hoc agent classes for different tasks, Layer 4 defines a single, immutable, battle-tested execution engine (**`StandardAgentChassis`**) that is dynamically parameterized at runtime via an injected **`RoleCard`** (defining identity, tool whitelist, and operational limits) and a surgical **`ContextPackage`** (compiled from the Lead Architect's `TaskContract` and Layer 2's `RepoGraph`).

Once spawned, an ephemeral worker executes a strictly bounded `Think -> Act -> Observe` loop over its own isolated working memory, verifies its work, emits a structured `WorkerReport`, and **self-destructs** (wiping its ephemeral memory and temporary context so zero context pollution leaks back to the Lead Architect).

### Strict Invariants (Non-Negotiable Rules)
1. **Single Immutable Chassis (`StandardAgentChassis`):** Every ephemeral worker—whether `SCOUT`, `SURGEON`, `INQUISITOR`, or a custom dynamic specialist—runs on the exact same `StandardAgentChassis` class. Behavior differences arise *exclusively* from the injected `RoleCard` and `ContextPackage`.
2. **Physical Tool Whitelisting:** A worker is physically blocked at the chassis level (`_on_tool_call`) from invoking any tool not explicitly listed in its `RoleCard.tools_whitelist`, even before reaching Layer 1's `WorkspaceGuard`.
   * `SCOUT` whitelist: `("read_file", "list_dir", "grep_search", "find_files")` — zero mutation or shell execution.
   * `SURGEON` whitelist: `("read_file", "list_dir", "grep_search", "find_files", "apply_patch", "run_command")` — mutations restricted to `ExecutionContext.allowed_files`.
   * `INQUISITOR` whitelist: `("read_file", "list_dir", "grep_search", "find_files", "run_command")` — zero file mutation (`apply_patch` is physically absent).
3. **Zero Shared-Memory Write Access:** Ephemeral workers have **no reference** to Layer 2's `Blackboard`. They write only to their local `EphemeralWorkingMemory` (which dies on termination) and propose discovered facts or report modifications strictly via their final `WorkerReport`.
4. **Sliding-Window Context Compression:** To prevent token explosion during multi-step exploration or debugging inside a worker, `EphemeralWorkingMemory` keeps the full output of only the most recent `sliding_window_size` tool observations (default: `4`), automatically compressing older tool observations into compact 2-line summaries while preserving the worker's `scratchpad_notes` intact.
5. **Truthful Artifact Reporting & Mandatory Lifecycle Hooks:** Every worker execution must pass through `_on_spawn()`, `_on_tool_call()`, and `_on_terminate()`. During `_on_terminate()`, the chassis deterministically overwrites/merges `WorkerReport.modified_files` with the actual set of files successfully mutated via `apply_patch` (preventing hallucinated file edits) and clears `EphemeralWorkingMemory`.

---

## 2. Directory Structure

```text
agent_os/
├── L1_KERNEL_SPEC.md
├── L2_BRAIN_SPEC.md
├── L3_ARCHITECT_SPEC.md
├── L4_SQUAD_SPEC.md
├── kernel/                        # Layer 1: Deterministic Kernel
├── brain/                         # Layer 2: Live Codebase Engine & Memory
├── architect/                     # Layer 3: The Lead Architect
├── squad/                         # Layer 4 (L4): Standard Agent Template & Ephemeral Squads
│   ├── __init__.py
│   ├── schemas.py                 # Pydantic v2 contracts for Chassis, RoleCard, ContextPackage, StepDecision
│   ├── role_cards.py              # Built-in RoleCard presets (SCOUT, SURGEON, INQUISITOR) + Custom Builder
│   ├── memory.py                  # EphemeralWorkingMemory (Sliding window + scratchpad + auto-wipe)
│   ├── chassis.py                 # StandardAgentChassis (Fixed ReAct engine & lifecycle hooks)
│   └── dispatcher.py              # EphemeralSquadDispatcher (Implements L3 WorkerDispatcher & spawns chassis)
├── agent_os.py                    # Top-level AgentOS Facade uniting L1 + L2 + L3 + L4
└── tests/
    ├── test_l1_kernel.py          # (10 tests)
    ├── test_l2_brain.py           # (6 tests)
    ├── test_l3_architect.py       # (7 tests)
    └── test_l4_squad.py           # Comprehensive verification suite for Layer 4 & End-to-End AgentOS
```

---

## 3. Core Data Contracts (`squad/schemas.py`)

All models must use Pydantic v2 with `ConfigDict(extra="forbid")`.

### 3.1 Role Card & Context Package Contracts
* **`RoleCard(BaseModel)`:**
  * `model_config = ConfigDict(extra="forbid", frozen=True)`
  * `role_id: Literal["SCOUT", "SURGEON", "INQUISITOR"]`
  * `persona_title: str` (e.g., `"Forensic Codebase Scout"`, `"Precision AST Code Surgeon"`, `"Adversarial QA Inquisitor"`)
  * `system_directive: str` (Core behavioral identity and rules injected into the chassis system prompt)
  * `tools_whitelist: tuple[str, ...]` (Exact tool names permitted from `ToolRegistry`)
  * `model_profile: ModelProfile = ModelProfile.FAST_WORKER`
  * `max_steps: int = Field(default=10, ge=1, le=30)`
  * `sliding_window_size: int = Field(default=4, ge=1, le=15)`
  * `require_test_execution_if_configured: bool = False`
* **`ContextPackage(BaseModel)`:**
  * `model_config = ConfigDict(extra="forbid")`
  * `contract: TaskContract` (From `architect.contracts`)
  * `target_file_skeletons: dict[str, str] = Field(default_factory=dict)` (Map of `rel_path -> RepoGraph.get_file_skeleton(rel_path)`)
  * `git_diff: str = ""` (Populated for `INQUISITOR` or post-mutation verification)
  * `custom_constraints: list[str] = Field(default_factory=list)`

### 3.2 Chassis Step & Tool Call Contracts
* **`ToolCallSpec(BaseModel)`:**
  * `model_config = ConfigDict(extra="forbid")`
  * `tool_name: Literal["read_file", "list_dir", "apply_patch", "grep_search", "find_files", "run_command"]`
  * `arguments: dict[str, Any] = Field(default_factory=dict)`
* **`FinalReportDraft(BaseModel)`:**
  * `model_config = ConfigDict(extra="forbid")`
  * `status: WorkerStatus`
  * `summary: str`
  * `discovered_facts: list[VerifiedFactInput] = Field(default_factory=list)`
  * `inquisitor_verdict: InquisitorVerdict = InquisitorVerdict.NOT_APPLICABLE`
  * `error_details: str | None = None`
* **`ChassisStepDecision(BaseModel)`:**
  * `model_config = ConfigDict(extra="forbid")`
  * `thought: str` (Internal reasoning for the current step)
  * `scratchpad_note: str | None = None` (Optional key finding to persist in the worker's scratchpad across sliding-window compression)
  * `action_type: Literal["CALL_TOOL", "FINISH_TASK"]`
  * `tool_call: ToolCallSpec | None = None` (Required when `action_type == "CALL_TOOL"`)
  * `final_report: FinalReportDraft | None = None` (Required when `action_type == "FINISH_TASK"`)

### 3.3 Ephemeral Memory Contracts
* **`InteractionRecord(BaseModel)`:**
  * `model_config = ConfigDict(extra="forbid")`
  * `step_number: int`
  * `thought: str`
  * `tool_name: str`
  * `tool_args_summary: str`
  * `tool_success: bool`
  * `raw_output: str`
  * `compressed_summary: str`

---

## 4. Component Specifications

### 4.1 Built-in Role Cards & Dynamic Specialist Builder (`squad/role_cards.py`)
* Define the 3 canonical `RoleCard` constants:
  1. **`SCOUT_ROLE_CARD`:**
     * `role_id="SCOUT"`
     * `persona_title="Forensic Codebase Scout"`
     * `tools_whitelist=("read_file", "list_dir", "grep_search", "find_files")`
     * `model_profile=ModelProfile.FAST_WORKER`
     * `max_steps=10`
     * Directive emphasizes locating exact line numbers (`start_line`, `end_line`) and returning evidence-backed `VerifiedFactInput` items without guessing.
  2. **`SURGEON_ROLE_CARD`:**
     * `role_id="SURGEON"`
     * `persona_title="Precision AST Code Surgeon"`
     * `tools_whitelist=("read_file", "list_dir", "grep_search", "find_files", "apply_patch", "run_command")`
     * `model_profile=ModelProfile.FAST_WORKER`
     * `max_steps=12`
     * `require_test_execution_if_configured=True`
     * Directive emphasizes reading exact target lines before patching, applying surgical `PatchBlock` edits only to `target_files`, and running `test_command` (if provided) before finishing.
  3. **`INQUISITOR_ROLE_CARD`:**
     * `role_id="INQUISITOR"`
     * `persona_title="Adversarial QA & Security Inquisitor"`
     * `tools_whitelist=("read_file", "list_dir", "grep_search", "find_files", "run_command")`
     * `model_profile=ModelProfile.REASONING`
     * `max_steps=8`
     * `require_test_execution_if_configured=True`
     * Directive emphasizes adversarial verification of `git_diff` against `acceptance_criteria`, running verification commands, and returning `InquisitorVerdict.APPROVED` only when 100% of criteria pass with zero regressions.
* **`get_role_card(role: Literal["SCOUT", "SURGEON", "INQUISITOR"], custom_persona_title: str | None = None, extra_directive: str | None = None, max_steps_override: int | None = None) -> RoleCard`:**
  * Returns the canonical `RoleCard` or creates a customized clone (e.g., customizing a `SURGEON` into `"FastAPI Endpoint Surgeon"` while strictly preserving the underlying `role_id` and security `tools_whitelist`).

---

### 4.2 Ephemeral Working Memory (`squad/memory.py`)
Class: `EphemeralWorkingMemory`
* **Initialization:** `EphemeralWorkingMemory(sliding_window_size: int = 4)`
* **State:**
  * `self.scratchpad: list[str] = []`
  * `self.history: list[InteractionRecord] = []`
  * `self.mutated_files: list[str] = []` (Deterministic ledger of files where `apply_patch` succeeded)
  * `self.commands_executed: list[str] = []`
* **Methods:**
  1. `add_note(note: str) -> None`: Appends non-empty note to `self.scratchpad`.
  2. `record_interaction(step_number: int, thought: str, tool_name: str, tool_args: dict[str, Any], result: ToolResult) -> None`:
     * Builds a deterministic 2-line `compressed_summary` of `result.output` (e.g., status + first line + character/line count).
     * Appends `InteractionRecord` to `self.history`.
     * If `tool_name == "apply_patch"` and `result.success`: records normalized `path` in `self.mutated_files` (if not already present).
     * If `tool_name == "run_command"`: records `command` string in `self.commands_executed`.
  3. `render_memory_context() -> str`:
     * Renders `self.scratchpad` in full.
     * For `self.history`:
       * Older entries outside the last `self.sliding_window_size` steps are rendered using **only** their compact `compressed_summary` (`[Step N - COMPRESSED]`).
       * Recent entries within the last `self.sliding_window_size` steps are rendered with their full `raw_output` (`[Step N - FULL OUTPUT]`).
  4. `clear() -> None`:
     * Wipes `self.scratchpad.clear()`, `self.history.clear()`, `self.mutated_files.clear()`, and `self.commands_executed.clear()`.

---

### 4.3 The Universal Agent Chassis (`squad/chassis.py`)
Class: `StandardAgentChassis`
* **Initialization:**
  ```python
  def __init__(
      self,
      role_card: RoleCard,
      context_pkg: ContextPackage,
      ctx: ExecutionContext,
      gateway: ModelGateway,
      tool_registry: ToolRegistry,
  ) -> None
  ```
* **Lifecycle Hooks & Execution Engine:**
  1. `_on_spawn(self) -> None`:
     * Initializes `self.memory = EphemeralWorkingMemory(sliding_window_size=self.role_card.sliding_window_size)`.
     * Sets `self.is_alive = True`.
  2. `_on_tool_call(self, tool_call: ToolCallSpec) -> ToolResult`:
     * **Chassis Whitelist Gate:** Checks `if tool_call.tool_name not in self.role_card.tools_whitelist:`.
       * If forbidden, returns `ToolResult(success=False, output=f"Tool '{tool_call.tool_name}' is forbidden for role '{self.role_card.role_id}'. Allowed tools: {list(self.role_card.tools_whitelist)}", error_code="PERMISSION_DENIED", execution_time_ms=0.0)` **without invoking `tool_registry`**.
     * Otherwise, dispatches `self.tool_registry.execute(tool_call.tool_name, self.ctx, **tool_call.arguments)`.
  3. `async def execute(self) -> WorkerReport`:
     * Calls `self._on_spawn()`.
     * Uses `try ... finally: self._on_terminate()` to guarantee memory destruction even if an exception occurs.
     * Runs loop `for step in range(1, self.role_card.max_steps + 1):`
       * Builds system prompt (from `RoleCard`, `tools_whitelist` schemas, and output instructions) and user prompt (from `ContextPackage` + `self.memory.render_memory_context()` + current step budget `step / max_steps`).
       * Calls `decision, _ = await self.gateway.generate_structured(profile=self.role_card.model_profile, messages=messages, response_model=ChassisStepDecision, task_id=self.ctx.task_id)`.
       * If `decision.scratchpad_note`: calls `self.memory.add_note(decision.scratchpad_note)`.
       * **If `decision.action_type == "FINISH_TASK"`:**
         * **Pre-Finish Verification Hook:** If `self.role_card.require_test_execution_if_configured` is `True`, `self.context_pkg.contract.test_command` is set, `len(self.memory.commands_executed) == 0`, and `step < self.role_card.max_steps`:
           * Intercept premature finish! Automatically execute `run_command(command=self.context_pkg.contract.test_command)` via `_on_tool_call`, record the result in `self.memory`, and continue to the next step so the worker inspects the test output before finishing.
         * Otherwise, compile and return `self._build_final_report(decision.final_report)`.
       * **If `decision.action_type == "CALL_TOOL"`:**
         * Validate `decision.tool_call` is present (if missing, record an error observation and continue).
         * Execute `result = self._on_tool_call(decision.tool_call)`.
         * Record interaction in `self.memory.record_interaction(...)`.
     * **If `max_steps` is exhausted without `FINISH_TASK`:**
       * Return a `WorkerReport` with `status=WorkerStatus.FAILED`, `summary=f"Worker exhausted maximum step budget ({self.role_card.max_steps}) without completing the contract."`, `modified_files=list(self.memory.mutated_files)`, and `inquisitor_verdict=InquisitorVerdict.REJECTED` (if `INQUISITOR`) or `NOT_APPLICABLE`.
  4. `_build_final_report(self, draft: FinalReportDraft | None) -> WorkerReport`:
     * Combines `draft` with deterministic truth from `self.memory.mutated_files` (so `WorkerReport.modified_files` always reflects actual disk mutations).
     * Ensures `inquisitor_verdict` defaults appropriately if `role_id == "INQUISITOR"`.
  5. `_on_terminate(self) -> None`:
     * Calls `self.memory.clear()`.
     * Sets `self.is_alive = False`.

---

### 4.4 Ephemeral Squad Dispatcher (`squad/dispatcher.py`)
Class: `EphemeralSquadDispatcher` (Implements `WorkerDispatcher` from `architect.dispatcher`)
* **Initialization:**
  ```python
  def __init__(
      self,
      workspace_root: Path,
      gateway: ModelGateway,
      repo_graph: RepoGraph | None = None,
      tool_registry: ToolRegistry | None = None,
  ) -> None
  ```
* **`async def dispatch(self, contract: TaskContract, ctx: ExecutionContext, git_diff: str = "") -> WorkerReport`:**
  1. Refreshes `self.repo_graph` (or uses current graph) and extracts `target_file_skeletons` for each file in `contract.target_files` via `self.repo_graph.get_file_skeleton(f)`.
  2. Selects the appropriate `RoleCard` via `get_role_card(contract.target_role)`.
  3. Builds `ContextPackage(contract=contract, target_file_skeletons=target_file_skeletons, git_diff=git_diff)`.
  4. Spawns a fresh `chassis = StandardAgentChassis(role_card=role_card, context_pkg=context_pkg, ctx=ctx, gateway=self.gateway, tool_registry=self.tool_registry)`.
  5. Awaits `report = await chassis.execute()`.
  6. Verifies `assert chassis.is_alive is False` (guaranteeing ephemeral destruction) and returns `report`.

---

### 4.5 Top-Level Agent OS Facade (`agent_os.py`)
Class: `AgentOS`
* Provides a clean, single-entry-point facade uniting all 4 layers:
  ```python
  class AgentOS:
      def __init__(
          self,
          workspace_root: Path,
          gateway: ModelGateway | None = None,
          max_ticks: int = 15,
          max_rollbacks: int = 4,
      ) -> None: ...

      async def execute_task(self, task_goal: str, task_id: str | None = None) -> TaskOutcome: ...
  ```
* Automatically wires `ModelGateway`, `RepoGraph`, `EphemeralSquadDispatcher`, and `LeadArchitectLoop`, and executes `await loop.run()`.

---

## 5. Verification & Acceptance Criteria (`tests/test_l4_squad.py`)

Layer 4 and the complete Agent OS are considered finished **only** when `pytest` passes 100% across all 4 test suites (`test_l1_kernel.py`, `test_l2_brain.py`, `test_l3_architect.py`, and `test_l4_squad.py`), covering:

1. **Test 1 — Chassis Physical Tool Whitelist Enforcement (`StandardAgentChassis`):**
   * Verify that a `SCOUT` chassis attempting to call `apply_patch` or `run_command` is immediately blocked by `_on_tool_call` with `PERMISSION_DENIED`.
   * Verify that an `INQUISITOR` chassis attempting to call `apply_patch` is blocked with `PERMISSION_DENIED`.
2. **Test 2 — Sliding-Window Memory Compression & Ephemeral Destruction (`EphemeralWorkingMemory`):**
   * Configure a worker with `sliding_window_size=2` and execute 5 sequential tool steps (`read_file` / `grep_search`).
   * Verify that `render_memory_context()` compresses steps 1, 2, and 3 into `[Step N - COMPRESSED]` summaries while keeping steps 4 and 5 as `[Step N - FULL OUTPUT]`, and keeps all `scratchpad` notes intact.
   * Verify that after `chassis.execute()` finishes, `_on_terminate()` has wiped `chassis.memory.history` and `chassis.memory.scratchpad` to length `0` and set `chassis.is_alive is False`.
3. **Test 3 — Mandatory Test Execution Hook & Truthful Modified Files Ledger (`SURGEON`):**
   * Configure a `SURGEON` with a `TaskContract` containing `test_command="pytest -q"`.
   * Simulate the LLM attempting to `FINISH_TASK` immediately after `apply_patch` without running `run_command`.
   * Verify the chassis intercepts the premature finish, auto-runs `test_command`, feeds the result into memory, and deterministically populates `WorkerReport.modified_files` from `memory.mutated_files` (ignoring any hallucinated file names).
4. **Test 4 — Step Budget Exhaustion Handling:**
   * Configure a chassis with `max_steps=3` where the mock LLM endlessly calls `read_file`.
   * Verify the chassis terminates cleanly after step 3 with `WorkerStatus.FAILED` and cleans up memory.
5. **Test 5 — Full 4-Layer End-to-End Autonomous Bugfix Cycle (`AgentOS.execute_task`):**
   * In a temporary Git repository containing a buggy Python module (`calculator.py` where `divide(a, b)` has a bug or `multiply(a, b)` adds instead of multiplies) and a failing `test_calculator.py`:
   * Run `AgentOS.execute_task("Fix the bug in calculator.py so pytest passes.")` using the full 4-layer stack (`LeadArchitectLoop` + `EphemeralSquadDispatcher` + `StandardAgentChassis` + `ToolRegistry` + `GitShadowCheckpointer` + `RepoGraph` + `Blackboard`).
   * Verify that `SCOUT` inspects the files, `SURGEON` patches `calculator.py` and runs `pytest`, `INQUISITOR` reviews the real `git_diff` and approves, and `AgentOS` returns `TaskOutcome(success=True, final_phase=TaskPhase.COMPLETED)` with the bug fixed on disk.
6. **Test 6 — Live DeepSeek End-to-End Ephemeral Worker Execution:**
   * With `DEEPSEEK_API_KEY` active, dispatch a real `SCOUT` (and/or `AgentOS` cycle) via `EphemeralSquadDispatcher` against a fixture module, verifying that DeepSeek drives `StandardAgentChassis` through real tool calls (`read_file`) and emits a valid `WorkerReport` with accurate `discovered_facts`.