"""Deterministic search tools: grep_search and find_files."""

import fnmatch
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from kernel.exceptions import PermissionDeniedError, SecurityViolationError
from kernel.guard import WorkspaceGuard
from kernel.schemas import ExecutionContext, ToolResult


def grep_search(
    ctx: ExecutionContext,
    query: str | None = None,
    sub_path: str = ".",
    is_regex: bool = False,
    max_results: int | None = 50,
    *,
    pattern: str | None = None,
    path: str | None = None,
    case_sensitive: bool = True,
    **kwargs: Any,
) -> ToolResult:
    """Search for query across workspace files using ripgrep if available or pure Python regex."""
    start_time = time.perf_counter()
    search_query = query if query is not None else (pattern or "")
    search_path = path if path is not None else sub_path
    limit = max_results if max_results is not None else 50
    try:
        guard = WorkspaceGuard(ctx.workspace_root)
        safe_path = guard.authorize_read(ctx, search_path)

        if not safe_path.exists():
            return ToolResult(
                success=False,
                output=f"Path does not exist: {search_path}",
                error_code="EXECUTION_ERROR",
                execution_time_ms=(time.perf_counter() - start_time) * 1000,
            )

        matches: list[str] = []
        rg_path = shutil.which("rg")

        if rg_path:
            rg_cmd = [
                rg_path,
                "--line-number",
                "--color", "never",
                "--max-count", str(limit),
            ]
            if not is_regex:
                rg_cmd.append("--fixed-strings")
            if not case_sensitive:
                rg_cmd.append("-i")
            rg_cmd.extend([search_query, str(safe_path)])

            try:
                proc = subprocess.run(
                    rg_cmd,
                    capture_output=True,
                    text=True,
                    cwd=str(ctx.workspace_root),
                    timeout=ctx.command_timeout_sec,
                )
                if proc.stdout:
                    for line in proc.stdout.splitlines():
                        if len(matches) >= limit:
                            break
                        # Truncate line length > 300
                        if len(line) > 300:
                            line = line[:297] + "..."
                        matches.append(line)
            except Exception:
                # Fall back to pure Python implementation if rg fails
                matches.clear()

        if not matches and not rg_path:
            # Pure Python fallback
            flags = 0 if case_sensitive else re.IGNORECASE
            pattern_str = search_query if is_regex else re.escape(search_query)
            try:
                compiled = re.compile(pattern_str, flags)
            except re.error as e:
                return ToolResult(
                    success=False,
                    output=f"Invalid regex pattern '{search_query}': {e}",
                    error_code="EXECUTION_ERROR",
                    execution_time_ms=(time.perf_counter() - start_time) * 1000,
                )

            search_root = safe_path if safe_path.is_dir() else safe_path.parent
            file_targets = [safe_path] if safe_path.is_file() else []

            if not file_targets:
                for root, dirs, files in os.walk(search_root):
                    curr_dir = Path(root)
                    # Filter blacklisted directories
                    dirs[:] = [d for d in dirs if guard._is_blacklisted(curr_dir / d) is None]
                    for f in sorted(files):
                        fp = curr_dir / f
                        if guard._is_blacklisted(fp) is None:
                            file_targets.append(fp)

            for fp in file_targets:
                if len(matches) >= limit:
                    break
                try:
                    with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                        for lineno, line in enumerate(f, start=1):
                            line_clean = line.rstrip("\r\n")
                            if compiled.search(line_clean):
                                try:
                                    rel = fp.relative_to(ctx.workspace_root.resolve()).as_posix()
                                except ValueError:
                                    rel = str(fp)
                                line_disp = line_clean[:297] + "..." if len(line_clean) > 300 else line_clean
                                matches.append(f"{rel}:{lineno}: {line_disp}")
                                if len(matches) >= limit:
                                    break
                except (OSError, UnicodeDecodeError):
                    continue

        output_str = "\n".join(matches) if matches else "No matches found."
        return ToolResult(
            success=True,
            output=output_str,
            execution_time_ms=(time.perf_counter() - start_time) * 1000,
            metadata={"match_count": len(matches), "capped_at": limit},
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
            output=f"Error during grep search: {e}",
            error_code="EXECUTION_ERROR",
            execution_time_ms=(time.perf_counter() - start_time) * 1000,
        )


def find_files(
    ctx: ExecutionContext,
    pattern: str,
    sub_path: str = ".",
    *,
    path: str | None = None,
    max_results: int | None = 100,
    **kwargs: Any,
) -> ToolResult:
    """Find files matching glob pattern while respecting blacklist rules."""
    start_time = time.perf_counter()
    target_path = path if path is not None else sub_path
    limit = max_results if max_results is not None else 100

    try:
        guard = WorkspaceGuard(ctx.workspace_root)
        safe_path = guard.authorize_read(ctx, target_path)

        if not safe_path.exists():
            return ToolResult(
                success=False,
                output=f"Path does not exist: {target_path}",
                error_code="EXECUTION_ERROR",
                execution_time_ms=(time.perf_counter() - start_time) * 1000,
            )

        found_files: list[str] = []
        search_root = safe_path if safe_path.is_dir() else safe_path.parent

        for root, dirs, files in os.walk(search_root):
            curr_dir = Path(root)
            dirs[:] = [d for d in dirs if guard._is_blacklisted(curr_dir / d) is None]

            for f in sorted(files):
                if len(found_files) >= limit:
                    break
                fp = curr_dir / f
                if guard._is_blacklisted(fp) is None and fnmatch.fnmatch(f, pattern):
                    rel = fp.relative_to(ctx.workspace_root.resolve()).as_posix()
                    found_files.append(rel)
            if len(found_files) >= limit:
                break

        return ToolResult(
            success=True,
            output="\n".join(found_files) if found_files else "No files matched.",
            execution_time_ms=(time.perf_counter() - start_time) * 1000,
            metadata={"match_count": len(found_files), "pattern": pattern},
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
            output=f"Error finding files matching '{pattern}': {e}",
            error_code="EXECUTION_ERROR",
            execution_time_ms=(time.perf_counter() - start_time) * 1000,
        )
