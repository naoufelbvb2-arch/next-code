# Engineering Specification: Layer 3 (L3) — The Lead Architect (`architect`)
**Project:** Micro-Engineering Agent OS  
**Phase:** Layer 3 (L3) Executive Reasoning, Mental Simulation & Deterministic Safety Gates  
**Python Version:** 3.11+  
**Core Validation Library:** Pydantic v2  
**Dependencies:** Layer 1 (`kernel`), Layer 2 (`brain`)

---

## 1. Architectural Philosophy & Invariants
Layer 3 (`architect`) is the **executive reasoning, mental simulation, and orchestration engine** of the Agent OS. It acts as a Principal Tech Lead: it never reads raw multi-hundred-line source files directly and never executes shell commands or file mutations itself. Instead, it maintains a clean-room context window fed by Layer 2's structural maps and digests, performs explicit mental simulation of risks, and dispatches isolated `TaskContract` objects to Layer 4 ephemeral workers via a `WorkerDispatcher` protocol.

### Strict Invariants (Non-Negotiable Rules)
1. **Clean-Room Context Isolation:** The Lead Architect has zero direct access to `filesystem`, `search`, or `shell` tools. Its context is strictly composed of:
   * `Blackboard.compile_context_digest()` (including the Graveyard of Failed Hypotheses).
   * `RepoGraph.render_repo_map()` and targeted `ImpactReport` / file skeletons.
   * `PlaybookStore.render_playbooks_prompt()`.
   * The structured `WorkerReport` returned from the most recently dispatched worker.
2. **Gate 1 — No Blind Surgery:** If the Architect attempts to `DISPATCH_SURGEON` with an empty `target_files` list, the deterministic loop must reject or intercept the decision before any worker is spawned.
3. **Gate 2 — Mandatory Pre-Surgery Shadow Checkpoint:** Before any `SURGEON` worker is dispatched, `LeadArchitectLoop` MUST deterministically invoke `GitShadowCheckpointer.create_checkpoint()` and record the hash on the `Blackboard`.
4. **Gate 3 — Automatic Rollback & Graveyard Recording:** If a `SURGEON` returns `status == WorkerStatus.FAILED`, OR if an `INQUISITOR` returns `verdict == InquisitorVerdict.REJECTED`:
   * The loop MUST deterministically invoke `checkpointer.rollback(pre_surgery_checkpoint)`.
   * The loop MUST invoke `blackboard.record_failed_hypothesis(...)` capturing the Architect's hypothesis, modified files, failure reason, and rolled-back checkpoint hash.
   * The loop MUST invoke `repo_graph.refresh()` so the structural graph immediately reflects the rolled-back workspace.
5. **Gate 4 — Mandatory Inquisitor Approval Before Completion:** If any code mutation (`DISPATCH_SURGEON`) has succeeded since the task began (or since the last rollback), the Architect is FORBIDDEN from transitioning to `COMPLETE_TASK` until a `DISPATCH_INQUISITOR` step has been executed and returned `verdict == InquisitorVerdict.APPROVED` on the current un-reverted state. If the LLM outputs `COMPLETE_TASK` prematurely after a `SURGEON` run, the deterministic gate in `LeadArchitectLoop` must override `next_action` to `DISPATCH_INQUISITOR` automatically.

---

## 2. Directory Structure & Minor L1 Polish

### 2.1 Minor Polish to Layer 1 (`kernel/checkpoint.py`)
* In `GitShadowCheckpointer.rollback()`, when parsing `diff-tree -r -z --name-status` records where `status.startswith(("R", "C"))` (Rename or Copy), ensure that `dst_path` is deleted for **both** `"R"` and `"C"` (`if status.startswith(("R", "C")) and dst_path:`) so renamed post-checkpoint files do not linger alongside restored original files.

### 2.2 Layer 3 File Tree
```text
agent_os/
├── L1_KERNEL_SPEC.md
├── L2_BRAIN_SPEC.md
├── L3_ARCHITECT_SPEC.md
├── kernel/                        # Layer 1 (Hardened)
├── brain/                         # Layer 2 (Hardened)
├── architect/                     # Layer 3 (L3)
│   ├── __init__.py
│   ├── contracts.py               # Pydantic v2 contracts (TaskContract, WorkerReport, ArchitectDecision...)
│   ├── prompts.py                 # System & tick prompt builders for Lead Architect
│   ├── dispatcher.py              # Abstract WorkerDispatcher protocol & ScriptedMockDispatcher for testing
│   └── lead_loop.py               # LeadArchitectLoop state machine & deterministic safety gates
└── tests/
    ├── test_l1_kernel.py
    ├── test_l2_brain.py
    └── test_l3_architect.py       # Comprehensive verification suite for Layer 3
```

---

## 3. Core Data Contracts (`architect/contracts.py`)

All models must use Pydantic v2 with `ConfigDict(extra="forbid")`.

### 3.1 Worker Dispatch & Report Contracts (L3 <-> L4 Bridge)
* **`WorkerStatus(str, Enum)`:**
  * `SUCCESS = "SUCCESS"`
  * `FAILED = "FAILED"`
  * `BLOCKED = "BLOCKED"`
* **`InquisitorVerdict(str, Enum)`:**
  * `APPROVED = "APPROVED"`
  * `REJECTED = "REJECTED"`
  * `NOT_APPLICABLE = "NOT_APPLICABLE"`
* **`VerifiedFactInput(BaseModel)`:**
  * `statement: str`
  * `evidence_file: str`
  * `start_line: int = Field(ge=1)`
  * `end_line: int = Field(ge=1)`
* **`TaskContract(BaseModel)`:**
  * `contract_id: str`
  * `target_role: Literal["SCOUT", "SURGEON", "INQUISITOR"]`
  * `objective: str` (Crisp, unambiguous instructions for the ephemeral worker)
  * `target_files: list[str] = Field(default_factory=list)` (Relative POSIX paths; becomes `allowed_files` in `ExecutionContext` when `target_role == "SURGEON"`)
  * `context_briefing: str` (Distilled architectural context and facts relevant to this step)
  * `acceptance_criteria: list[str] = Field(default_factory=list)`
  * `test_command: str | None = None` (Optional shell command for Surgeon/Inquisitor to verify changes)
* **`WorkerReport(BaseModel)`:**
  * `contract_id: str`
  * `worker_role: Literal["SCOUT", "SURGEON", "INQUISITOR"]`
  * `status: WorkerStatus`
  * `summary: str` (Concise report of findings, edits made, or verification results)
  * `discovered_facts: list[VerifiedFactInput] = Field(default_factory=list)`
  * `modified_files: list[str] = Field(default_factory=list)`
  * `inquisitor_verdict: InquisitorVerdict = InquisitorVerdict.NOT_APPLICABLE`
  * `error_details: str | None = None`

### 3.2 Architect Cognitive & Decision Contracts
* **`ArchitectAction(str, Enum)`:**
  * `DISPATCH_SCOUT = "DISPATCH_SCOUT"`
  * `DISPATCH_SURGEON = "DISPATCH_SURGEON"`
  * `DISPATCH_INQUISITOR = "DISPATCH_INQUISITOR"`
  * `COMPLETE_TASK = "COMPLETE_TASK"`
  * `ABORT_TASK = "ABORT_TASK"`
* **`MentalSimulation(BaseModel)`:**
  * `hypothesis: str` (What we believe is happening or why this change will solve the goal)
  * `predicted_side_effects: list[str] = Field(default_factory=list)` (Potential blast-radius risks on dependent modules)
  * `verification_strategy: str` (How we will prove the hypothesis succeeded)
* **`ArchitectDecision(BaseModel)`:**
  * `situation_assessment: str`
  * `mental_simulation: MentalSimulation`
  * `new_phase: TaskPhase`
  * `updated_plan_steps: list[PlanStep] = Field(default_factory=list)` (If non-empty, updates the Blackboard tactical plan)
  * `new_facts_to_record: list[VerifiedFactInput] = Field(default_factory=list)`
  * `next_action: ArchitectAction`
  * `task_contract: TaskContract | None = None` (Required whenever `next_action` is `DISPATCH_*`)
  * `final_response: str | None = None` (Summary when `COMPLETE_TASK` or `ABORT_TASK` is chosen)
* **`TaskOutcome(BaseModel)`:**
  * `task_id: str`
  * `success: bool`
  * `final_phase: TaskPhase`
  * `summary: str`
  * `total_ticks: int`
  * `rollbacks_triggered: int`
  * `final_checkpoint_hash: str | None = None`

---

## 4. Component Specifications

### 4.1 Worker Dispatcher Protocol (`architect/dispatcher.py`)
* **`WorkerDispatcher(Protocol)`:**
  * Defines `async def dispatch(self, contract: TaskContract, ctx: ExecutionContext, git_diff: str = "") -> WorkerReport:`
* **`ScriptedMockDispatcher`:**
  * A deterministic test/simulation implementation of `WorkerDispatcher` that accepts a queue or callback of handlers (`Callable[[TaskContract, ExecutionContext, str], Awaitable[WorkerReport] | WorkerReport]`).
  * Allows unit and integration tests of Layer 3 to simulate file mutations, test failures, rollbacks, and Inquisitor rejections deterministically before Layer 4 is attached.

---

### 4.2 Prompt Compiler (`architect/prompts.py`)
* **`build_architect_system_prompt() -> str`:**
  * Defines the identity and ironclad rules of the Principal Lead Architect:
    * Never guess file contents; dispatch `SCOUT` to gather exact line numbers and facts first.
    * Never repeat any approach listed in the `⚠️ GRAVEYARD OF FAILED HYPOTHESES`.
    * Always examine the `ImpactReport` of target files during `mental_simulation`.
    * Always dispatch `SURGEON` with explicit `target_files` and clear `acceptance_criteria`.
    * Always dispatch `INQUISITOR` to review diffs and run verification suites before completing a mutating task.
* **`build_architect_tick_prompt(...) -> str`:**
  * Combines into a clean, token-budgeted prompt:
    1. Matched Procedural Playbooks (`PlaybookStore.render_playbooks_prompt`).
    2. Blackboard Context Digest (`Blackboard.compile_context_digest`).
    3. Structural Repository Map (`RepoGraph.render_repo_map` focused on active plan files).
    4. Blast-Radius Impact Reports (`RepoGraph.get_impact_radius`) for any `target_files` in current `IN_PROGRESS` or `PENDING` plan steps.
    5. Latest `WorkerReport` (if a worker was dispatched in the previous tick).
    6. Gate Override Warnings (if the deterministic loop intercepted an illegal action in the previous tick).

---

### 4.3 Lead Architect Loop & Deterministic Gates (`architect/lead_loop.py`)
Class: `LeadArchitectLoop`
* **Initialization:**
  ```python
  def __init__(
      self,
      workspace_root: Path,
      gateway: ModelGateway,
      dispatcher: WorkerDispatcher,
      task_id: str,
      task_goal: str,
      max_ticks: int = 15,
      max_rollbacks: int = 4,
  ) -> None
  ```
  * Instantiates `RepoGraph(workspace_root)`, `Blackboard(workspace_root, task_id, task_goal)`, `PlaybookStore(workspace_root)`, and `GitShadowCheckpointer(workspace_root)`.
  * Tracks internal flags:
    * `self.unverified_mutation_pending: bool = False` (Set to `True` when a `SURGEON` succeeds; set to `False` only when an `INQUISITOR` approves or a rollback occurs).
    * `self.last_pre_surgery_checkpoint: str | None = None`
    * `self.last_surgery_hypothesis: str = ""`
    * `self.last_surgery_files: list[str] = []`
    * `self.rollbacks_triggered: int = 0`

* **Execution Cycle (`async def run(self) -> TaskOutcome`):**
  1. Refresh `RepoGraph` (`self.repo_graph.refresh()`).
  2. Create an initial baseline shadow checkpoint via `self.checkpointer.create_checkpoint("task_baseline")` and record it in `self.blackboard`.
  3. Match relevant playbooks via `self.playbooks.match_playbooks(self.task_goal)`.
  4. **For `tick` in `range(1, self.max_ticks + 1)`:**
     * Build messages (`system_prompt` + `tick_prompt`) and call:
       `decision, usage = await self.gateway.generate_structured(task_id=self.task_id, profile=ModelProfile.REASONING, messages=messages, response_model=ArchitectDecision)`
     * Apply Blackboard updates from `decision`:
       * Update phase (`set_phase`), plan steps (`set_plan` if `updated_plan_steps` is non-empty), and record any `new_facts_to_record`.
     * **Enforce Deterministic Safety Gates:**
       * **Gate 4 Check (Mandatory Inquisitor):** If `decision.next_action == ArchitectAction.COMPLETE_TASK` and `self.unverified_mutation_pending is True`:
         * Do NOT complete the task! Override `decision.next_action = ArchitectAction.DISPATCH_INQUISITOR` and auto-construct a verification `TaskContract` targeting `self.last_surgery_files` to inspect the git diff and verify acceptance criteria.
       * **Completion / Abort Check:**
         * If `decision.next_action == ArchitectAction.COMPLETE_TASK` (and `unverified_mutation_pending is False`): set phase to `COMPLETED` and return `TaskOutcome(success=True, ...)`.
         * If `decision.next_action == ArchitectAction.ABORT_TASK`: set phase to `FAILED` and return `TaskOutcome(success=False, ...)`.
       * **Gate 1 Check (No Blind Surgery):**
         * If `decision.next_action == ArchitectAction.DISPATCH_SURGEON` and (not `decision.task_contract` or not `decision.task_contract.target_files`):
           * Record a gate warning note and force retry on the next tick without dispatching the Surgeon.
     * **Prepare Execution Context & Pre-Surgery Checkpoint (Gate 2):**
       * Map `decision.next_action` to `AgentRole` (`SCOUT`, `SURGEON`, or `INQUISITOR`).
       * If role is `SURGEON`:
         * Create shadow checkpoint: `cp_hash = self.checkpointer.create_checkpoint(f"pre_surgery_tick_{tick}")`.
         * `self.blackboard.record_checkpoint(cp_hash)`
         * `self.last_pre_surgery_checkpoint = cp_hash`
         * `self.last_surgery_hypothesis = decision.mental_simulation.hypothesis`
         * `self.last_surgery_files = list(decision.task_contract.target_files)`
       * Construct `ExecutionContext(task_id=self.task_id, role=role, workspace_root=self.workspace_root, allowed_files=frozenset(decision.task_contract.target_files) if role == AgentRole.SURGEON else frozenset())`.
       * Compute `git_diff = self.checkpointer.get_diff(self.last_pre_surgery_checkpoint)` if `self.last_pre_surgery_checkpoint` exists (especially useful for `INQUISITOR`).
     * **Dispatch Worker:**
       * `report = await self.dispatcher.dispatch(decision.task_contract, ctx, git_diff=git_diff)`
       * Record any `report.discovered_facts` into `self.blackboard.add_fact(...)`.
       * Refresh `self.repo_graph.refresh()`.
     * **Evaluate Report & Trigger Auto-Rollback + Graveyard (Gate 3):**
       * **Case A — Surgeon Succeeded:**
         * If `report.worker_role == "SURGEON"` and `report.status == WorkerStatus.SUCCESS`:
           * `self.unverified_mutation_pending = True`
           * Update `self.last_surgery_files` with `report.modified_files or self.last_surgery_files`.
       * **Case B — Surgeon Failed:**
         * If `report.worker_role == "SURGEON"` and `report.status != WorkerStatus.SUCCESS`:
           * Trigger `_execute_rollback_and_graveyard(reason=report.error_details or report.summary)`.
       * **Case C — Inquisitor Rejected:**
         * If `report.worker_role == "INQUISITOR"` and (`report.inquisitor_verdict == InquisitorVerdict.REJECTED` or `report.status == WorkerStatus.FAILED`):
           * Trigger `_execute_rollback_and_graveyard(reason=f"Inquisitor Rejected: {report.error_details or report.summary}")`.
       * **Case D — Inquisitor Approved:**
         * If `report.worker_role == "INQUISITOR"` and `report.inquisitor_verdict == InquisitorVerdict.APPROVED` and `report.status == WorkerStatus.SUCCESS`:
           * `self.unverified_mutation_pending = False`
     * **Max Rollbacks Check:**
       * If `self.rollbacks_triggered >= self.max_rollbacks`: set phase to `FAILED` and return `TaskOutcome(success=False, summary="Exceeded maximum allowed rollbacks.", ...)`.
  5. If loop exhausts `max_ticks`, set phase to `FAILED` and return `TaskOutcome(success=False, summary="Exceeded maximum allowed ticks.", ...)`.

* **Helper `_execute_rollback_and_graveyard(self, reason: str) -> None`:**
  * Calls `self.checkpointer.rollback(self.last_pre_surgery_checkpoint)` (if set).
  * Calls `self.blackboard.record_failed_hypothesis(hypothesis=self.last_surgery_hypothesis, modified_files=self.last_surgery_files, failure_reason=reason, rolled_back_to_checkpoint=self.last_pre_surgery_checkpoint)`.
  * Resets `self.unverified_mutation_pending = False`.
  * Increments `self.rollbacks_triggered += 1`.
  * Calls `self.repo_graph.refresh()`.

---

## 5. Verification & Acceptance Criteria (`tests/test_l3_architect.py`)

Layer 3 is considered complete **only** when `tests/test_l1_kernel.py`, `tests/test_l2_brain.py`, and `tests/test_l3_architect.py` pass 100%, covering:

1. **Test 1 — Rename Rollback Cleanup Polish (`kernel/checkpoint.py`):**
   * Verify that renaming a tracked file (`git mv` or file move) after a checkpoint and calling `rollback()` restores the original file AND deletes the renamed destination file cleanly.
2. **Test 2 — Happy Path Orchestration Cycle (`Scout -> Surgeon -> Inquisitor -> Complete`):**
   * Using a deterministic mock LLM provider + `ScriptedMockDispatcher`, verify `LeadArchitectLoop` dispatches `SCOUT` (records facts to Blackboard), dispatches `SURGEON` (takes pre-surgery checkpoint and mutates file via `apply_patch`), dispatches `INQUISITOR` (receives non-empty `git_diff` and returns `APPROVED`), and finishes with `TaskOutcome(success=True, final_phase=TaskPhase.COMPLETED)`.
3. **Test 3 — Gate 4 Enforcement (Premature Completion Blocked Before Inquisitor):**
   * Simulate the Architect LLM attempting to output `COMPLETE_TASK` immediately after `SURGEON` succeeds (skipping `INQUISITOR`).
   * Verify `LeadArchitectLoop` intercepts the premature completion, automatically overrides the action to `DISPATCH_INQUISITOR`, and only completes after `INQUISITOR` approves.
4. **Test 4 — Gate 3 Enforcement on Surgeon Failure (Auto-Rollback & Graveyard):**
   * Simulate a `SURGEON` modifying a workspace file and then failing (`WorkerStatus.FAILED`).
   * Verify `LeadArchitectLoop` automatically rolls back the workspace file to its pre-surgery state, records a `FailedHypothesis` in `Blackboard`, increments `rollbacks_triggered`, and injects the graveyard warning into the next tick's prompt.
5. **Test 5 — Gate 3 Enforcement on Inquisitor Rejection (Auto-Rollback & Re-Plan):**
   * Simulate a `SURGEON` succeeding on disk, followed by `INQUISITOR` returning `InquisitorVerdict.REJECTED` (e.g., due to an edge-case bug).
   * Verify the file modification is rolled back on disk, the rejection reason is recorded in the Blackboard's `failed_hypotheses` graveyard, and the Architect succeeds on the subsequent attempt.
6. **Test 6 — Gate 1 Enforcement (No Blind Surgery Without Target Files) & Max Rollbacks Abort:**
   * Verify that attempting `DISPATCH_SURGEON` with `target_files=[]` is blocked without invoking the dispatcher.
   * Verify that exceeding `max_rollbacks` cleanly terminates the loop with `TaskOutcome(success=False, final_phase=TaskPhase.FAILED)`.
7. **Test 7 — Live DeepSeek Architect Reasoning Integration:**
   * With `DEEPSEEK_API_KEY` configured, run a 1-tick or 2-tick live `LeadArchitectLoop` against a small fixture repo where DeepSeek generates a valid structured `ArchitectDecision` (including `MentalSimulation`, `PlanStep` list, and `TaskContract` for `SCOUT`).