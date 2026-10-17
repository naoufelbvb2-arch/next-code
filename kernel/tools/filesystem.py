"""Deterministic filesystem tools with syntax gates and atomic writes."""

import ast
import json
import os
import tempfile
import time
from pathlib import Path

from kernel.exceptions import PermissionDeniedError, SecurityViolationError
from kernel.guard import WorkspaceGuard
from kernel.schemas import ExecutionContext, PatchBlock, ToolResult


def read_file(
    ctx: ExecutionContext,
    path: str,
    start_line: int | None = 1,
    end_line: int | None = 250,
) -> ToolResult:
    """Read a slice of a file within the workspace safely."""
    start_time = time.perf_counter()
    try:
        if start_line is None:
            start_line = 1
        if end_line is None:
            end_line = start_line + 249

        if start_line < 1:
            return ToolResult(
                success=False,
                output="start_line must be greater than or equal to 1.",
                error_code="EXECUTION_ERROR",
                execution_time_ms=(time.perf_counter() - start_time) * 1000,
            )

        if end_line < start_line:
            return ToolResult(
                success=False,
                output=f"end_line ({end_line}) cannot be less than start_line ({start_line}).",
                error_code="EXECUTION_ERROR",
                execution_time_ms=(time.perf_counter() - start_time) * 1000,
            )

        if (end_line - start_line) > 300:
            return ToolResult(
                success=False,
                output=f"Line span of {end_line - start_line} exceeds maximum allowed span of 300 lines.",
                error_code="EXECUTION_ERROR",
                execution_time_ms=(time.perf_counter() - start_time) * 1000,
            )

        guard = WorkspaceGuard(ctx.workspace_root)
        safe_path = guard.authorize_read(ctx, path)

        if not safe_path.exists():
            return ToolResult(
                success=False,
                output=f"File not found: {path}",
                error_code="EXECUTION_ERROR",
                execution_time_ms=(time.perf_counter() - start_time) * 1000,
            )

        if not safe_path.is_file():
            return ToolResult(
                success=False,
                output=f"Path is not a regular file: {path}",
                error_code="EXECUTION_ERROR",
                execution_time_ms=(time.perf_counter() - start_time) * 1000,
            )

        with open(safe_path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()

        total_lines = len(all_lines)
        # 1-indexed line extraction
        slice_lines = all_lines[start_line - 1 : end_line]
        formatted_lines = [
            f"{start_line + i:4d} | {line.rstrip('\r\n')}"
            for i, line in enumerate(slice_lines)
        ]

        return ToolResult(
            success=True,
            output="\n".join(formatted_lines),
            execution_time_ms=(time.perf_counter() - start_time) * 1000,
            metadata={
                "total_lines": total_lines,
                "start_line": start_line,
                "end_line": min(end_line, total_lines),
                "path": str(safe_path.relative_to(ctx.workspace_root.resolve())),
            },
        )
    except SecurityViolationError as e:
        return ToolResult(
            success=False,
            output=str(e),
            error_code="PATH_VIOLATION",
            execution_time_ms=(time.perf_counter() - start_time) * 1000,
        )
    except PermissionDeniedError as e:
        return ToolResult(
            success=False,
            output=str(e),
            error_code="PERMISSION_DENIED",
            execution_time_ms=(time.perf_counter() - start_time) * 1000,
        )
    except Exception as e:
        return ToolResult(
            success=False,
            output=f"Unexpected error reading file '{path}': {e}",
            error_code="EXECUTION_ERROR",
            execution_time_ms=(time.perf_counter() - start_time) * 1000,
        )


def list_dir(
    ctx: ExecutionContext,
    path: str = ".",
    max_depth: int | None = 2,
    recursive: bool | None = None,
) -> ToolResult:
    """List directory structure ignoring blacklisted paths and git internals."""
    start_time = time.perf_counter()
    try:
        if max_depth is None:
            max_depth = 2
        if recursive is True and max_depth == 2:
            max_depth = 4
        elif recursive is False and max_depth == 2:
            max_depth = 1

        guard = WorkspaceGuard(ctx.workspace_root)
        safe_path = guard.authorize_read(ctx, path)

        if not safe_path.exists():
            return ToolResult(
                success=False,
                output=f"Directory does not exist: {path}",
                error_code="EXECUTION_ERROR",
                execution_time_ms=(time.perf_counter() - start_time) * 1000,
            )

        if not safe_path.is_dir():
            return ToolResult(
                success=False,
                output=f"Path is not a directory: {path}",
                error_code="EXECUTION_ERROR",
                execution_time_ms=(time.perf_counter() - start_time) * 1000,
            )

        lines: list[str] = []
        base_depth = len(safe_path.parts)
        entry_count = 0

        for root, dirs, files in os.walk(safe_path):
            current_path = Path(root)
            depth = len(current_path.parts) - base_depth
            if depth >= max_depth:
                dirs.clear()
                continue

            # Filter out blacklisted directories in-place so os.walk does not descend
            allowed_dirs = []
            for d in list(dirs):
                dir_path = current_path / d
                if guard._is_blacklisted(dir_path) is None:
                    allowed_dirs.append(d)
            dirs[:] = allowed_dirs

            indent = "  " * depth
            rel_root = current_path.relative_to(ctx.workspace_root.resolve()).as_posix()
            if rel_root != ".":
                lines.append(f"{indent}[DIR] {current_path.name}/")

            for f in sorted(files):
                file_path = current_path / f
                if guard._is_blacklisted(file_path) is None:
                    entry_count += 1
                    lines.append(f"{indent}  {f}")

        return ToolResult(
            success=True,
            output="\n".join(lines) if lines else "(empty directory)",
            execution_time_ms=(time.perf_counter() - start_time) * 1000,
            metadata={"entry_count": entry_count, "max_depth": max_depth},
        )
    except SecurityViolationError as e:
        return ToolResult(
            success=False,
            output=str(e),
            error_code="PATH_VIOLATION",
            execution_time_ms=(time.perf_counter() - start_time) * 1000,
        )
    except PermissionDeniedError as e:
        return ToolResult(
            success=False,
            output=str(e),
            error_code="PERMISSION_DENIED",
            execution_time_ms=(time.perf_counter() - start_time) * 1000,
        )
    except Exception as e:
        return ToolResult(
            success=False,
            output=f"Error listing directory '{path}': {e}",
            error_code="EXECUTION_ERROR",
            execution_time_ms=(time.perf_counter() - start_time) * 1000,
        )


def apply_patch(
    ctx: ExecutionContext,
    path: str,
    patches: list[PatchBlock],
    create_if_missing: bool | None = False,
) -> ToolResult:
    """Apply sequential patches to a file with AST/JSON pre-mutation syntax gates and atomic writes."""
    start_time = time.perf_counter()
    if create_if_missing is None:
        create_if_missing = False
    try:
        guard = WorkspaceGuard(ctx.workspace_root)
        safe_path = guard.authorize_write(ctx, path)

        if not safe_path.exists():
            if not create_if_missing:
                return ToolResult(
                    success=False,
                    output=f"Target file does not exist and create_if_missing is False: {path}",
                    error_code="EXECUTION_ERROR",
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                )
            content = ""
        else:
            if not safe_path.is_file():
                return ToolResult(
                    success=False,
                    output=f"Target path is not a regular file: {path}",
                    error_code="EXECUTION_ERROR",
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                )
            content = safe_path.read_text(encoding="utf-8")

        # Normalize line endings
        working_content = content.replace("\r\n", "\n")

        parsed_patches: list[PatchBlock] = [
            PatchBlock.model_validate(p) if isinstance(p, dict) else p
            for p in patches
        ]

        for idx, block in enumerate(parsed_patches):
            norm_search = block.search.replace("\r\n", "\n")
            norm_replace = block.replace.replace("\r\n", "\n")

            if norm_search == "" and working_content != "":
                return ToolResult(
                    success=False,
                    output=f"Patch conflict in block {idx + 1}: Search block cannot be empty when patching a non-empty file.",
                    error_code="PATCH_CONFLICT",
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                )

            match_count = working_content.count(norm_search)
            if match_count == 0:
                return ToolResult(
                    success=False,
                    output=f"Patch conflict in block {idx + 1}: search block not found in '{path}'.\nSearch block:\n{norm_search}",
                    error_code="PATCH_CONFLICT",
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                )
            if match_count > 1:
                return ToolResult(
                    success=False,
                    output=f"Patch conflict in block {idx + 1}: Search block is ambiguous; matches {match_count} locations in '{path}'. Include more surrounding context lines.",
                    error_code="PATCH_CONFLICT",
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                )

            working_content = working_content.replace(norm_search, norm_replace, 1)

        # Syntax Verification Gate (Pre-Write)
        normalized_path_str = Path(path).as_posix()
        if normalized_path_str.endswith(".py"):
            try:
                ast.parse(working_content, filename=path)
            except SyntaxError as e:
                return ToolResult(
                    success=False,
                    output=f"SyntaxError in Python file '{path}': {e.msg} at line {e.lineno}, offset {e.offset}\n{e.text}",
                    error_code="SYNTAX_ERROR",
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    metadata={
                        "lineno": e.lineno,
                        "offset": e.offset,
                        "msg": e.msg,
                        "text": e.text,
                    },
                )

        if normalized_path_str.endswith(".json"):
            try:
                json.loads(working_content)
            except json.JSONDecodeError as e:
                return ToolResult(
                    success=False,
                    output=f"JSONDecodeError in file '{path}': {e.msg} at line {e.lineno}, column {e.colno}",
                    error_code="SYNTAX_ERROR",
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                    metadata={"lineno": e.lineno, "colno": e.colno, "msg": e.msg},
                )

        # Atomic Write via temporary file in the same directory
        safe_path.parent.mkdir(parents=True, exist_ok=True)
        temp_fd, temp_file_path = tempfile.mkstemp(
            prefix="tmp_patch_",
            dir=safe_path.parent,
            text=True,
        )
        try:
            with open(temp_fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(working_content)
            os.replace(temp_file_path, safe_path)
        except Exception:
            if os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            raise

        return ToolResult(
            success=True,
            output=f"Successfully applied {len(patches)} patch(es) to '{path}'.",
            execution_time_ms=(time.perf_counter() - start_time) * 1000,
            metadata={"patches_applied": len(patches), "path": path},
        )
    except SecurityViolationError as e:
        return ToolResult(
            success=False,
            output=str(e),
            error_code="PATH_VIOLATION",
            execution_time_ms=(time.perf_counter() - start_time) * 1000,
        )
    except PermissionDeniedError as e:
        return ToolResult(
            success=False,
            output=str(e),
            error_code="PERMISSION_DENIED",
            execution_time_ms=(time.perf_counter() - start_time) * 1000,
        )
    except Exception as e:
        return ToolResult(
            success=False,
            output=f"Unexpected error applying patch to '{path}': {e}",
            error_code="EXECUTION_ERROR",
            execution_time_ms=(time.perf_counter() - start_time) * 1000,
        )
