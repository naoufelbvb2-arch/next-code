"""Sliding-window ephemeral working memory for StandardAgentChassis."""

import json
from pathlib import Path
from typing import Any

from kernel.schemas import ToolResult
from squad.schemas import InteractionRecord


class EphemeralWorkingMemory:
    """Isolated, step-bounded working memory featuring sliding-window context compression."""

    def __init__(self, sliding_window_size: int = 4) -> None:
        self.sliding_window_size = sliding_window_size
        self.scratchpad: list[str] = []
        self.history: list[InteractionRecord] = []
        self.mutated_files: list[str] = []
        self.commands_executed: list[str] = []

    def add_note(self, note: str) -> None:
        """Append a concise key observation to the scratchpad if non-empty."""
        clean_note = note.strip()
        if clean_note and clean_note not in self.scratchpad:
            self.scratchpad.append(clean_note)

    def record_interaction(
        self,
        step_number: int,
        thought: str,
        tool_name: str,
        tool_args: dict[str, Any],
        result: ToolResult,
    ) -> None:
        """Record tool execution, compute compressed summary, and update ledger state."""
        # 1. Deterministic 2-line compressed summary
        status_str = "SUCCESS" if result.success else f"FAILED ({result.error_code or 'ERROR'})"
        lines = result.output.splitlines()
        line_count = len(lines)
        char_count = len(result.output)
        preview = lines[0].strip() if lines else "(Empty output)"
        if len(preview) > 120:
            preview = preview[:117] + "..."
        compressed = f"Status: {status_str} | Lines: {line_count} | Chars: {char_count}\nPreview: {preview}"

        # 2. Tool args summary
        try:
            tool_args_summary = json.dumps(tool_args, sort_keys=True, default=str)
        except Exception:
            tool_args_summary = str(tool_args)

        # 3. Create record
        record = InteractionRecord(
            step_number=step_number,
            thought=thought,
            tool_name=tool_name,
            tool_args_summary=tool_args_summary,
            tool_success=result.success,
            raw_output=result.output,
            compressed_summary=compressed,
        )
        self.history.append(record)

        # 4. Deterministic mutation ledger
        if tool_name == "apply_patch" and result.success:
            path_arg = tool_args.get("path")
            if path_arg:
                norm_path = Path(path_arg).as_posix()
                if norm_path not in self.mutated_files:
                    self.mutated_files.append(norm_path)

        # 5. Command ledger
        if tool_name == "run_command":
            cmd_arg = tool_args.get("command")
            if cmd_arg and cmd_arg not in self.commands_executed:
                self.commands_executed.append(cmd_arg)

    def render_memory_context(self) -> str:
        """Render working memory context using sliding-window compression for older steps."""
        sections: list[str] = []

        # 1. Scratchpad
        if self.scratchpad:
            notes_str = "\n".join(f"- {note}" for note in self.scratchpad)
            sections.append(f"[SCRATCHPAD NOTES]\n{notes_str}")
        else:
            sections.append("[SCRATCHPAD NOTES]\n(No scratchpad notes recorded)")

        # 2. History with sliding-window compression
        if not self.history:
            sections.append("[INTERACTION HISTORY]\n(No previous steps)")
        else:
            history_blocks: list[str] = []
            total_steps = len(self.history)
            cutoff = max(0, total_steps - self.sliding_window_size)

            for idx, rec in enumerate(self.history):
                if idx < cutoff:
                    history_blocks.append(
                        f"[Step {rec.step_number} - COMPRESSED] Tool: {rec.tool_name}({rec.tool_args_summary})\n"
                        f"Thought: {rec.thought}\n"
                        f"{rec.compressed_summary}"
                    )
                else:
                    history_blocks.append(
                        f"[Step {rec.step_number} - FULL OUTPUT] Tool: {rec.tool_name}({rec.tool_args_summary})\n"
                        f"Thought: {rec.thought}\n"
                        f"Output:\n"
                        f"{rec.raw_output}"
                    )

            sections.append("[INTERACTION HISTORY]\n" + "\n\n".join(history_blocks))

        return "\n\n".join(sections)

    def clear(self) -> None:
        """Completely wipe all internal state upon worker termination."""
        self.scratchpad.clear()
        self.history.clear()
        self.mutated_files.clear()
        self.commands_executed.clear()
