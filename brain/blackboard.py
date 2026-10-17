"""Persistent working memory, hypothesis graveyard, and context digest compiler."""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from brain.schemas import (
    BlackboardState,
    FailedHypothesis,
    PlanStep,
    StepStatus,
    TaskPhase,
    VerifiedFact,
)


class Blackboard:
    """Crash-resilient persistent working memory with atomic state writes and anti-amnesia graveyard."""

    def __init__(
        self,
        workspace_root: Path,
        task_id: str,
        task_goal: str = "",
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.task_id = task_id
        self.state_dir = self.workspace_root / ".agent_os" / "state"
        self.state_file = self.state_dir / f"{task_id}.json"

        if self.state_file.exists():
            try:
                raw = self.state_file.read_text(encoding="utf-8")
                self.state = BlackboardState.model_validate_json(raw)
                if task_goal and not self.state.task_goal:
                    self.state.task_goal = task_goal
                    self._persist()
            except Exception:
                self.state = BlackboardState(task_id=task_id, task_goal=task_goal)
                self._persist()
        else:
            self.state = BlackboardState(task_id=task_id, task_goal=task_goal)
            self._persist()

    def _persist(self) -> None:
        """Atomically persist state to disk using tempfile + os.replace."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        raw_json = self.state.model_dump_json(indent=2)

        temp_fd, temp_path = tempfile.mkstemp(
            prefix="tmp_bb_",
            dir=self.state_dir,
            text=True,
        )
        try:
            with open(temp_fd, "w", encoding="utf-8") as f:
                f.write(raw_json)
            os.replace(temp_path, self.state_file)
        except Exception:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise

    def set_phase(self, phase: TaskPhase) -> None:
        """Transition task lifecycle phase."""
        self.state.phase = phase
        self._persist()

    def record_checkpoint(self, checkpoint_hash: str) -> None:
        """Record active checkpoint and append to checkpoint history."""
        self.state.active_checkpoint_hash = checkpoint_hash
        self.state.checkpoint_history.append(checkpoint_hash)
        self._persist()

    def add_fact(
        self,
        statement: str,
        evidence_file: str,
        start_line: int,
        end_line: int,
        discovered_by: str = "SCOUT",
    ) -> VerifiedFact:
        """Add an evidence-backed verified fact, avoiding exact duplicates."""
        norm_file = Path(evidence_file).as_posix()

        # Check for existing identical fact
        for existing in self.state.verified_facts:
            if (
                existing.statement == statement
                and existing.evidence_file == norm_file
                and existing.start_line == start_line
                and existing.end_line == end_line
            ):
                return existing

        fact_id = f"fact_{len(self.state.verified_facts) + 1:03d}"
        fact = VerifiedFact(
            fact_id=fact_id,
            statement=statement,
            evidence_file=norm_file,
            start_line=start_line,
            end_line=end_line,
            discovered_by=discovered_by,
        )
        self.state.verified_facts.append(fact)
        self._persist()
        return fact

    def set_plan(self, steps: list[PlanStep]) -> None:
        """Set or overwrite the active plan steps."""
        self.state.plan_steps = list(steps)
        self._persist()

    def update_step_status(
        self,
        step_id: str,
        status: StepStatus,
        result_summary: str | None = None,
    ) -> None:
        """Update status and optional outcome summary of a specific plan step."""
        for step in self.state.plan_steps:
            if step.step_id == step_id:
                step.status = status
                if result_summary is not None:
                    step.result_summary = result_summary
                self._persist()
                return

    def record_failed_hypothesis(
        self,
        hypothesis: str,
        modified_files: list[str],
        failure_reason: str,
        rolled_back_to_checkpoint: str | None = None,
    ) -> FailedHypothesis:
        """Record an execution failure in the anti-amnesia graveyard."""
        attempt_number = len(self.state.failed_hypotheses) + 1
        timestamp_iso = datetime.now(timezone.utc).isoformat()
        norm_files = [Path(f).as_posix() for f in modified_files]

        failed_entry = FailedHypothesis(
            attempt_number=attempt_number,
            hypothesis=hypothesis,
            modified_files=norm_files,
            failure_reason=failure_reason,
            rolled_back_to_checkpoint=rolled_back_to_checkpoint,
            timestamp_iso=timestamp_iso,
        )
        self.state.failed_hypotheses.append(failed_entry)
        self._persist()
        return failed_entry

    def add_note(self, note: str) -> None:
        """Append an unverified insight or scratchpad note."""
        self.state.notes.append(note)
        self._persist()

    def compile_context_digest(self, max_chars: int = 6000) -> str:
        """Compile a prioritized Markdown digest highlighting the graveyard of failed hypotheses."""
        header_lines = [
            "# Task Context Digest",
            f"- **Task ID:** {self.state.task_id}",
            f"- **Goal:** {self.state.task_goal or '(Not specified)'}",
            f"- **Phase:** {self.state.phase.value}",
            f"- **Active Checkpoint:** `{self.state.active_checkpoint_hash or 'None'}`",
            "",
        ]

        # 1. ⚠️ GRAVEYARD OF FAILED HYPOTHESES (Highest priority to prevent repeated mistakes)
        graveyard_lines = [
            "## ⚠️ GRAVEYARD OF FAILED HYPOTHESES (DO NOT REPEAT THESE)",
        ]
        if not self.state.failed_hypotheses:
            graveyard_lines.append("*(No failed hypotheses recorded yet)*\n")
        else:
            for fh in self.state.failed_hypotheses:
                files_str = ", ".join(fh.modified_files) if fh.modified_files else "None"
                rollback_str = f"`{fh.rolled_back_to_checkpoint}`" if fh.rolled_back_to_checkpoint else "None"
                graveyard_lines.extend(
                    [
                        f"### Attempt #{fh.attempt_number} [FAILED]",
                        f"- **Hypothesis:** {fh.hypothesis}",
                        f"- **Files Modified:** {files_str}",
                        f"- **Failure Reason:** {fh.failure_reason}",
                        f"- **Rolled Back To:** {rollback_str}",
                        f"- **Timestamp:** {fh.timestamp_iso}",
                        "",
                    ]
                )

        # 2. Tactical Plan Steps
        plan_lines = ["## Tactical Plan Steps"]
        if not self.state.plan_steps:
            plan_lines.append("*(No plan steps defined)*\n")
        else:
            for step in self.state.plan_steps:
                target_str = ", ".join(step.target_files) if step.target_files else "General"
                plan_lines.append(
                    f"- [{step.status.value}] `{step.step_id}`: {step.description} (Role: {step.assigned_role}, Target: {target_str})"
                )
                if step.result_summary:
                    plan_lines.append(f"  * Result: {step.result_summary}")
            plan_lines.append("")

        # 3. Verified Evidence & Facts
        facts_lines = ["## Verified Evidence & Facts"]
        if not self.state.verified_facts:
            facts_lines.append("*(No verified facts established)*\n")
        else:
            for fact in self.state.verified_facts:
                facts_lines.append(
                    f"- [{fact.fact_id}] {fact.statement} (Citation: `{fact.evidence_file}:{fact.start_line}-{fact.end_line}`, by {fact.discovered_by})"
                )
            facts_lines.append("")

        # 4. Working Notes
        notes_lines = ["## Working Notes"]
        if not self.state.notes:
            notes_lines.append("*(No notes)*\n")
        else:
            for note in self.state.notes:
                notes_lines.append(f"- {note}")
            notes_lines.append("")

        # Assemble with budget management (preserving header and graveyard intact)
        essential_block = "\n".join(header_lines) + "\n".join(graveyard_lines)
        remainder = "\n".join(plan_lines) + "\n".join(facts_lines) + "\n".join(notes_lines)

        if len(essential_block) + len(remainder) <= max_chars:
            return essential_block + remainder

        # Trim remainder if needed while keeping essential block
        available = max_chars - len(essential_block) - 60
        if available > 0:
            trimmed_remainder = remainder[:available] + "\n... [REMAINDER OF DIGEST TRUNCATED] ..."
            return essential_block + trimmed_remainder

        return essential_block
