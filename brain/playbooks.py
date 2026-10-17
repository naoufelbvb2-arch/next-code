"""Procedural Skill Bank and PlaybookStore for standard operating procedures."""

import json
import os
import re
import tempfile
from pathlib import Path

from brain.schemas import Playbook, TaskPhase


BUILTIN_PLAYBOOKS: list[Playbook] = [
    Playbook(
        playbook_id="pb_bugfix_tdd",
        title="Root-Cause Bugfix & Regression Guard",
        trigger_keywords=[
            "bug",
            "fix",
            "error",
            "exception",
            "traceback",
            "fail",
            "broken",
            "failing",
            "reproduce",
            "regression",
            "patch",
            "crash",
            "syntaxerror",
        ],
        applicable_phases=[
            TaskPhase.EXPLORING,
            TaskPhase.PLANNING,
            TaskPhase.EXECUTING,
            TaskPhase.VERIFYING,
        ],
        sop_steps=[
            "Reproduce the issue with a minimal failing unit test or command before modifying source code.",
            "Inspect root cause using read_file and grep_search without guessing.",
            "Formulate precise, minimal patch applying AST/JSON syntax-gated mutations via SURGEON.",
            "Verify both the new test and full test suite pass before marking step passed.",
        ],
        verification_rules=[
            "Failing test must fail before fix and pass after fix.",
            "Zero regression in existing test suite.",
        ],
        anti_patterns=[
            "Do not mutate source code without a reproducing test.",
            "Do not blind-guess fixes without inspecting the exact traceback and AST lines.",
            "Do not delete or comment out existing tests to make the suite pass.",
        ],
    ),
    Playbook(
        playbook_id="pb_feature_impl",
        title="Clean Feature Implementation & Contract Alignment",
        trigger_keywords=[
            "implement",
            "create",
            "add",
            "feature",
            "build",
            "module",
            "new",
            "contract",
            "schema",
            "extend",
        ],
        applicable_phases=[
            TaskPhase.PLANNING,
            TaskPhase.EXECUTING,
            TaskPhase.VERIFYING,
        ],
        sop_steps=[
            "Define and verify strict Pydantic v2 data contracts before writing logic.",
            "Check impact radius of new symbols and dependencies using RepoGraph.",
            "Implement components atomically in dependency order.",
            "Write comprehensive unit tests covering edge cases and error contracts.",
        ],
        verification_rules=[
            "All data contracts must validate with extra='forbid'.",
            "100% test pass on new functionality.",
        ],
        anti_patterns=[
            "Do not introduce circular imports or unregistered global state.",
            "Do not leave placeholder methods, pass, or TODO comments.",
        ],
    ),
    Playbook(
        playbook_id="pb_safe_refactor",
        title="Impact-Aware Structural Refactoring",
        trigger_keywords=[
            "refactor",
            "rename",
            "clean",
            "optimize",
            "restructure",
            "simplify",
            "extract",
            "deduplicate",
        ],
        applicable_phases=[
            TaskPhase.EXPLORING,
            TaskPhase.PLANNING,
            TaskPhase.EXECUTING,
            TaskPhase.VERIFYING,
        ],
        sop_steps=[
            "Create a GitShadowCheckpointer snapshot before initiating any refactor.",
            "Compute impact radius for all target files to identify dependents.",
            "Apply patches incrementally, running tests after each atomic change.",
            "Rollback immediately if any contract breaks or unexpected regression occurs.",
        ],
        verification_rules=[
            "Public API contracts and function signatures must remain backwards-compatible unless explicitly breaking.",
            "Full test suite must pass without regressions.",
        ],
        anti_patterns=[
            "Do not combine refactoring with behavioral changes or feature additions in the same step.",
            "Do not refactor without an active shadow checkpoint.",
        ],
    ),
]


class PlaybookStore:
    """Storage and keyword matching engine for procedural engineering playbooks."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()
        self.playbooks_dir = self.workspace_root / ".agent_os" / "playbooks"
        self.playbooks_dir.mkdir(parents=True, exist_ok=True)
        self._seed_builtins()

    def _seed_builtins(self) -> None:
        """Seed the 3 core engineering playbooks if not already saved."""
        for pb in BUILTIN_PLAYBOOKS:
            pb_file = self.playbooks_dir / f"{pb.playbook_id}.json"
            if not pb_file.exists():
                self.save_playbook(pb)

    def save_playbook(self, playbook: Playbook) -> None:
        """Persist playbook as atomic JSON file."""
        self.playbooks_dir.mkdir(parents=True, exist_ok=True)
        pb_file = self.playbooks_dir / f"{playbook.playbook_id}.json"
        raw_json = playbook.model_dump_json(indent=2)

        temp_fd, temp_path = tempfile.mkstemp(
            prefix="tmp_pb_",
            dir=self.playbooks_dir,
            text=True,
        )
        try:
            with open(temp_fd, "w", encoding="utf-8") as f:
                f.write(raw_json)
            os.replace(temp_path, pb_file)
        except Exception:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise

    def get_playbook(self, playbook_id: str) -> Playbook | None:
        """Load a playbook by its ID."""
        pb_file = self.playbooks_dir / f"{playbook_id}.json"
        if not pb_file.exists():
            return None
        try:
            raw = pb_file.read_text(encoding="utf-8")
            return Playbook.model_validate_json(raw)
        except Exception:
            return None

    def list_playbooks(self) -> list[Playbook]:
        """List all playbooks sorted by playbook_id."""
        playbooks: list[Playbook] = []
        for file in sorted(self.playbooks_dir.glob("*.json")):
            try:
                raw = file.read_text(encoding="utf-8")
                playbooks.append(Playbook.model_validate_json(raw))
            except Exception:
                continue
        return playbooks

    def match_playbooks(
        self,
        task_description: str,
        phase: TaskPhase | None = None,
        top_k: int = 2,
    ) -> list[Playbook]:
        """Score playbooks deterministically based on trigger keyword overlap and task phase."""
        desc_lower = task_description.lower()
        tokens = set(re.findall(r"[a-z0-9_]+", desc_lower))

        scored: list[tuple[int, str, Playbook]] = []

        for pb in self.list_playbooks():
            if phase is not None and phase not in pb.applicable_phases:
                continue

            score = 0
            for kw in pb.trigger_keywords:
                kw_clean = kw.lower()
                if kw_clean in tokens:
                    score += 2  # Exact token match
                elif kw_clean in desc_lower:
                    score += 1  # Substring match

            if score > 0:
                # Deterministic tie-breaker using playbook_id
                scored.append((score, pb.playbook_id, pb))

        scored.sort(key=lambda x: (-x[0], x[1]))
        return [item[2] for item in scored[:top_k]]

    def render_playbooks_prompt(self, playbooks: list[Playbook]) -> str:
        """Format matched playbooks into directive Markdown for prompt injection."""
        if not playbooks:
            return ""

        sections = ["# 📘 MANDATORY PROCEDURAL PLAYBOOKS (Follow These Instructions Strictly)\n"]

        for pb in playbooks:
            sections.append(f"## [{pb.playbook_id}] {pb.title}")

            sections.append("### Standard Operating Procedure (SOP Steps):")
            for idx, step in enumerate(pb.sop_steps, start=1):
                sections.append(f"{idx}. {step}")

            sections.append("### Mandatory Verification Rules:")
            for rule in pb.verification_rules:
                sections.append(f"- {rule}")

            sections.append("### Forbidden Anti-Patterns:")
            for anti in pb.anti_patterns:
                sections.append(f"- ⚠️ {anti}")

            sections.append("")

        return "\n".join(sections).strip()
