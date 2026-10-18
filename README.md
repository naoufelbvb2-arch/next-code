# Next Code — Micro-Engineering Agent OS

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Pydantic v2](https://img.shields.io/badge/validation-Pydantic%20v2-green.svg)](https://docs.pydantic.dev/)
[![Tests Passing](https://img.shields.io/badge/tests-30%2F30%20passing-brightgreen.svg)](tests/)
[![Architecture](https://img.shields.io/badge/architecture-Deterministic%204--Layer%20Kernel-purple.svg)](#system-architecture)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Next Code (`agent_os`)** is a deterministic, gate-driven multi-agent operating system designed for autonomous software engineering. Unlike conventional autonomous coding agents that hallucinate changes, corrupt dirty working trees, or loop infinitely on broken assumptions, Next Code enforces mathematical and structural guarantees across every phase of software development.

---

## Key Highlights & Core Invariants

- **Zero Blind Surgery:** No agent can mutate a file without prior reconnaissance and an explicit, authorized target file contract.
- **Dirty-Tree Safe Shadow Checkpointing:** Low-level Git plumbing snapshots working trees using isolated temporary index files (`GIT_INDEX_FILE`) without touching `HEAD` or destroying uncommitted developer changes.
- **Deterministic Auto-Rollback & Graveyard:** When a code modification fails unit tests or is rejected by the independent verifier, the workspace is automatically rolled back to its pre-surgery shadow checkpoint in milliseconds. The failed hypothesis and reason are permanently recorded in the **Blackboard Graveyard** so the squad never repeats the same mistake.
- **Ephemeral Worker Isolation:** Specialists (`SCOUT`, `SURGEON`, `INQUISITOR`) are spawned on clean universal chassis, execute strictly bounded tasks, report results, and have their conversation memory completely wiped upon termination.
- **Strict Role-Based Access Control (RBAC):** Read and write permissions are enforced at the OS level by physical security guards. A `SCOUT` can never mutate files or execute destructive commands; a `SURGEON` can only patch files explicitly listed in its authorized contract.

---

## System Architecture

Next Code is structured into four deterministic, decoupled layers:

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        Top-Level Facade (AgentOS)                      │
│                  agent_os = AgentOS(workspace_root)                    │
│            await agent_os.execute_task("Fix bug in pricing.py")        │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│              Layer 3 (L3): The Lead Architect (architect)              │
│  • Cognitive State Machine Loop (EXPLORING -> PLANNING -> EXECUTING)  │
│  • Gate 1: No Blind Surgery                                           │
│  • Gate 2: Mandatory Pre-Surgery Shadow Checkpoint                     │
│  • Gate 3: Auto-Rollback & Graveyard of Failed Hypotheses              │
│  • Gate 4: Mandatory Inquisitor Verification Gate                      │
└──────────────┬──────────────────────────────────────────┬──────────────┘
               │                                          │
┌──────────────▼───────────────────────────┐ ┌────────────▼──────────────┐
│  Layer 2 (L2): Live Codebase & Memory    │ │ Layer 4 (L4): Squads       │
│  • RepoGraph: AST symbol extraction,     │ │ • StandardAgentChassis     │
│    dependency graph & impact radius      │ │ • Ephemeral Workers:       │
│  • Blackboard: Shared state, plan steps, │ │   - SCOUT (Recon)          │
│    verified facts & failure graveyard    │ │   - SURGEON (Patching)     │
│  • PlaybookStore: Curated repair recipes │ │   - INQUISITOR (Testing)   │
└──────────────┬───────────────────────────┘ └────────────┬──────────────┘
               │                                          │
┌──────────────▼──────────────────────────────────────────▼──────────────┐
│               Layer 1 (L1): The Deterministic Kernel (kernel)          │
│  • WorkspaceGuard: Zero Path Escape, Symlink Jailing & Blacklisting    │
│  • GitShadowCheckpointer: Plumbing Checkpoints & Zero-Touch Rollback   │
│  • Sandboxed Tools: Syntax-Gated apply_patch, run_command, filesystem  │
│  • ModelGateway & TokenLedger: DeepSeek / OpenAI Unified Gateway       │
└────────────────────────────────────────────────────────────────────────┘
```

---

### Layer 1: The Deterministic Kernel (`kernel/`)
The zero-agency execution foundation:
- **`WorkspaceGuard` (`guard.py`):** Path normalization, symlink jail enforcement, and case-insensitive blacklist matching (`.git/`, `.env*`, `.agent_os/`).
- **`GitShadowCheckpointer` (`checkpoint.py`):** Snapshots both tracked and untracked files via low-level Git plumbing (`write-tree`, `read-tree`, `diff-tree`). Rollback restores the workspace to the exact checkpoint tree in milliseconds while leaving `.agent_os/` state untouched.
- **Sandboxed Tools (`tools/`):**
  - `apply_patch`: Atomic, syntax-verified code patching (pre-validates Python AST and JSON syntax before touching disk).
  - `run_command`: Process-group isolated shell runner with timeout and output truncation safeguards.
  - `read_file`, `list_dir`, `grep_search`, `find_files`: Safe, sandboxed read-only exploration utilities.
- **`ModelGateway` & `TokenLedger` (`gateway/`):** Thread-safe, async-safe LLM gateway routing requests to reasoning (`deepseek-reasoner`) or worker (`deepseek-chat`) models with budget enforcement.

---

### Layer 2: Live Codebase Engine & Memory (`brain/`)
The codebase intelligence and persistence substrate:
- **`RepoGraph` (`repo_graph.py`):** Parses AST to extract classes, functions, async functions, and imports. Computes blast radius and impact radius for any target file.
- **`Blackboard` (`blackboard.py`):** Persistent working memory stored under `.agent_os/state/<task_id>.json`. Tracks cognitive phase, plan steps, evidence-backed facts, and the **Graveyard of Failed Hypotheses**.
- **`PlaybookStore` (`playbooks.py`):** Stores and seeds curated playbooks (e.g., Bug Fix, Feature Addition, Structural Refactoring) with semantic keyword matching.

---

### Layer 3: The Lead Architect (`architect/`)
The cognitive orchestration engine:
- **`LeadArchitectLoop` (`lead_loop.py`):** Executes structured reasoning ticks using DeepSeek Reasoner.
- **The 4 Non-Negotiable Safety Gates:**
  1. **Gate 1 (No Blind Surgery):** Blocks SURGEON dispatch if target files are missing or unexamined.
  2. **Gate 2 (Mandatory Pre-Surgery Checkpoint):** Creates a shadow checkpoint before any file modification.
  3. **Gate 3 (Auto-Rollback & Graveyard):** Intercepts worker failures or test rejections, rolls back workspace immediately, and logs the failure to memory.
  4. **Gate 4 (Mandatory Verification Gate):** Intercepts premature task completion and mandates independent `INQUISITOR` verification against acceptance criteria.

---

### Layer 4: Standard Agent Template & Ephemeral Squads (`squad/`)
The execution chassis:
- **`StandardAgentChassis` (`chassis.py`):** Universal execution engine for all sub-agents. Features sliding-window memory compression, strict physical tool whitelisting, and an auto-pre-finish test execution hook.
- **Role Cards (`role_cards.py`):**
  - **`SCOUT`:** Read-only reconnaissance (`read_file`, `list_dir`, `grep_search`, `find_files`).
  - **`SURGEON`:** Surgical mutations strictly confined to `allowed_files` (`read_file`, `apply_patch`, `run_command`).
  - **`INQUISITOR`:** Adversarial verification and test validation (`read_file`, `run_command`, `list_dir`, `grep_search`).
- **`EphemeralSquadDispatcher` (`dispatcher.py`):** Spawns agents on demand and guarantees that agent memory is destroyed on exit.

---

## Installation & Setup

### 1. Prerequisites
- **Python:** 3.11 or higher
- **Git:** 2.25 or higher

### 2. Clone and Install
```bash
git clone https://github.com/naoufelbvb2-arch/next-code.git
cd next-code

# Create virtual environment
python -m venv .venv

# Activate virtual environment
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Linux / macOS:
source .venv/bin/activate

# Install dependencies in editable mode
pip install -e ".[dev]"
```

### 3. Configure API Key
Set your DeepSeek API key as an environment variable (or export it in your shell):

```powershell
# Windows PowerShell
$env:DEEPSEEK_API_KEY = "your-deepseek-api-key"
```

```bash
# Linux / macOS
export DEEPSEEK_API_KEY="your-deepseek-api-key"
```

---

## Interactive CLI & Live Demo

Next Code includes a single-command CLI runner (`run.py`):

### 1. Run the Automated Sandbox Demo
Sets up an isolated demo repository with a reproducible bug and failing test, then watches the squad autonomously scout, patch, verify, and complete the mission:

```bash
python run.py --demo
```

### 2. Run on Your Own Repository
Target any workspace directory with a custom engineering objective:

```bash
python run.py "Find and fix the bug in calculator.py so pytest passes" --workspace ./my_project --max-ticks 15
```

---

## Programmatic Usage

You can embed the `AgentOS` facade directly in your own applications:

```python
import asyncio
from pathlib import Path
from agent_os import AgentOS

async def main():
    # Initialize AgentOS for your target repository
    agent_os = AgentOS(
        workspace_root=Path("./my_project"),
        max_ticks=15,
        max_rollbacks=4,
        verbose=True,
    )

    # Execute autonomous engineering task
    outcome = await agent_os.execute_task(
        task_goal="Fix the ZeroDivisionError in math_utils.py and verify all pytest tests pass."
    )

    if outcome.success:
        print(f"Task Succeeded in {outcome.total_ticks} ticks!")
        print(f"Summary: {outcome.summary}")
    else:
        print(f"Task Failed: {outcome.summary}")

if __name__ == "__main__":
    asyncio.run(main())
```

---

## Verification & Testing Suite

Next Code includes 30 tests covering every layer and security boundary:

```bash
# Run the complete test suite
python -m pytest -v tests/test_l1_kernel.py tests/test_l2_brain.py tests/test_l3_architect.py tests/test_l4_squad.py
```

### Test Coverage Breakdown:
| Test Module | Coverage Scope | Tests |
| :--- | :--- | :---: |
| [`test_l1_kernel.py`](tests/test_l1_kernel.py) | Path Escape, RBAC, Syntax Gating, Shadow Checkpointing, Subprocess Isolation, Token Ledger | 10 |
| [`test_l2_brain.py`](tests/test_l2_brain.py) | AST Parsing, Dependency Resolution, Impact Radius, Blackboard Recovery, Playbooks | 6 |
| [`test_l3_architect.py`](tests/test_l3_architect.py) | 4 Safety Gates, Rename Rollbacks, Auto-Rollback to Graveyard, DeepSeek Reasoning | 7 |
| [`test_l4_squad.py`](tests/test_l4_squad.py) | Chassis Whitelisting, Sliding Memory, Truthful Ledger, Autonomous Bugfix, Tool Signatures | 7 |
| **Total** | **Comprehensive Full-Stack Verification** | **30 / 30** |

---

## Security & Safety Architecture

1. **Zero Path Escape:** `WorkspaceGuard` rejects any path resolving outside `workspace_root` or accessing blacklisted folders (`.git`, `.env*`, `.agent_os`).
2. **Secrets Protection:** Comprehensive `.gitignore` rules prevent accidental commits of `.env` files, API keys, or memory ledgers.
3. **Atomic Operations:** All file modifications are validated through AST syntax checking before touching disk, eliminating half-written or corrupted files.
4. **Isolated Checkpoint Index:** Checkpoint staging uses temporary, isolated Git index files, preventing any alteration of user staging areas or `HEAD`.

---

## Directory Structure

```text
next-code/
├── README.md                 # Project Overview & Architecture Guide
├── pyproject.toml            # Python build configuration & dependencies
├── agent_os.py               # Unified top-level AgentOS Facade
├── run.py                    # Interactive CLI runner & sandbox demo
├── L1_KERNEL_SPEC.md         # Layer 1 Architectural Specification
├── L2_BRAIN_SPEC.md          # Layer 2 Architectural Specification
├── L3_ARCHITECT_SPEC.md      # Layer 3 Architectural Specification
├── L4_SQUAD_SPEC.md          # Layer 4 Architectural Specification
├── kernel/                   # Layer 1: Deterministic Kernel
│   ├── config.py             # KernelConfig & default settings
│   ├── exceptions.py         # Deterministic kernel exception hierarchy
│   ├── guard.py              # WorkspaceGuard & RBAC enforcement
│   ├── checkpoint.py         # GitShadowCheckpointer plumbing engine
│   ├── schemas.py            # Kernel data contracts (ToolResult, ExecutionContext)
│   ├── gateway/              # LLM ModelGateway & TokenLedger
│   └── tools/                # Sandboxed tools (filesystem, search, shell, registry)
├── brain/                    # Layer 2: Live Codebase Engine & Memory
│   ├── schemas.py            # AST SymbolNode, FileNode, BlackboardState
│   ├── repo_graph.py         # AST parser, dependency graph & blast radius
│   ├── blackboard.py         # Working memory & Graveyard of Failed Hypotheses
│   └── playbooks.py          # Curated playbook store & semantic matcher
├── architect/                # Layer 3: The Lead Architect
│   ├── contracts.py          # ArchitectDecision, TaskContract, WorkerReport
│   ├── prompts.py            # System prompt & context digest compilation
│   ├── dispatcher.py         # WorkerDispatcher protocol & mock dispatcher
│   └── lead_loop.py          # State machine loop & 4 Deterministic Safety Gates
├── squad/                    # Layer 4: Standard Agent Template & Ephemeral Squads
│   ├── schemas.py            # RoleCard, ContextPackage, ToolCallSpec
│   ├── memory.py             # EphemeralWorkingMemory with sliding-window compression
│   ├── role_cards.py         # SCOUT, SURGEON, INQUISITOR role definitions
│   ├── chassis.py            # StandardAgentChassis ReAct execution engine
│   └── dispatcher.py         # EphemeralSquadDispatcher
└── tests/                    # Comprehensive Verification Suite
    ├── test_l1_kernel.py     # 10 Kernel tests
    ├── test_l2_brain.py      # 6 Brain tests
    ├── test_l3_architect.py  # 7 Architect tests
    └── test_l4_squad.py      # 7 Squad tests
```

---

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
