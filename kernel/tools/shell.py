"""Deterministic sandboxed shell execution with process group isolation and smart truncation."""

import os
import signal
import subprocess
import sys
import time
from typing import Any

from kernel.execution import binding, check_cancelled, TaskCancelled, CleanupUnverified
from kernel.exceptions import PermissionDeniedError, SecurityViolationError
from kernel.guard import WorkspaceGuard
from kernel.schemas import ExecutionContext, ToolResult


def _truncate_output(
    text: str,
    max_len: int = 12000,
    head_len: int = 4000,
    tail_len: int = 6000,
) -> str:
    """Apply smart truncation preserving head and tail contexts."""
    if len(text) <= max_len:
        return text
    truncated_chars = len(text) - (head_len + tail_len)
    return f"{text[:head_len]}\n\n... [TRUNCATED {truncated_chars} CHARS] ...\n\n{text[-tail_len:]}"


def _kill_process_tree(proc: subprocess.Popen[Any]) -> None:
    """Terminate the process and all descendant processes across POSIX and Windows."""
    if sys.platform != "win32":
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                proc.kill()
            except Exception:
                pass
    else:
        # Windows process tree termination
        try:
            import psutil
            parent = psutil.Process(proc.pid)
            children = parent.children(recursive=True)
            for child in children:
                try:
                    child.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            parent.kill()
        except Exception:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    check=False,
                )
            except Exception:
                pass
            try:
                proc.kill()
            except Exception:
                pass


def run_command(
    ctx: ExecutionContext,
    command: str,
    cwd_rel: str = ".",
    *,
    cwd: str | None = None,
    timeout_sec: int | None = None,
    **kwargs: Any,
) -> ToolResult:
    """Execute shell command inside an isolated OS process group with strict timeouts and smart output truncation."""
    start_time = time.perf_counter()
    control = binding.get().control
    check_cancelled()
    target_cwd = cwd if cwd is not None else cwd_rel
    timeout = timeout_sec if timeout_sec is not None else ctx.command_timeout_sec

    try:
        guard = WorkspaceGuard(ctx.workspace_root)
        guard.authorize_command(ctx, command)
        safe_cwd = guard.resolve_safe_path(target_cwd)

        # Platform-specific process group isolation
        popen_kwargs: dict[str, Any] = {
            "cwd": str(safe_cwd),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "shell": True,
        }

        if sys.platform != "win32":
            popen_kwargs["start_new_session"] = True
        else:
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

        check_cancelled()
        job = None
        if control is not None and sys.platform == "win32":
            from kernel.owned_process import WindowsToolJob
            job = WindowsToolJob()
            popen_kwargs["creationflags"] |= 0x4  # CREATE_SUSPENDED before Job assignment
        try:
            proc = subprocess.Popen(command, **popen_kwargs)
            if job is not None:
                try:
                    job.assign_and_resume(proc)
                except BaseException:
                    proc.kill()
                    proc.wait(timeout=5)
                    raise
        except BaseException:
            if job is not None:
                job.stop_and_verify()
            raise

        try:
            if control is None:
                stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout)
            else:
                deadline = time.monotonic() + timeout
                while True:
                    control.check()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, timeout)
                    try:
                        stdout_bytes, stderr_bytes = proc.communicate(timeout=min(0.05, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        if time.monotonic() >= deadline:
                            raise
                control.check()
            elapsed_ms = (time.perf_counter() - start_time) * 1000

            stdout_str = stdout_bytes.decode("utf-8", errors="replace").strip()
            stderr_str = stderr_bytes.decode("utf-8", errors="replace").strip()

            if stdout_str and stderr_str:
                combined_output = f"{stdout_str}\n\n[STDERR]\n{stderr_str}"
            elif stdout_str:
                combined_output = stdout_str
            else:
                combined_output = stderr_str

            truncated_output = _truncate_output(combined_output)
            is_success = proc.returncode == 0

            return ToolResult(
                success=is_success,
                output=truncated_output,
                error_code=None if is_success else "EXECUTION_ERROR",
                execution_time_ms=elapsed_ms,
                metadata={
                    "returncode": proc.returncode,
                    "raw_length": len(combined_output),
                    "pid": proc.pid,
                },
            )

        except TaskCancelled:
            if job is not None:
                job.stop_and_verify()
            else:
                _kill_process_tree(proc)
            proc.communicate(timeout=5)
            raise
        except subprocess.TimeoutExpired:
            if job is not None:
                job.stop_and_verify()
            else:
                _kill_process_tree(proc)
            partial_stdout, partial_stderr = proc.communicate()
            elapsed_ms = (time.perf_counter() - start_time) * 1000

            out_str = (partial_stdout or b"").decode("utf-8", errors="replace").strip()
            err_str = (partial_stderr or b"").decode("utf-8", errors="replace").strip()
            if out_str and err_str:
                partial_output = f"{out_str}\n\n[STDERR]\n{err_str}"
            elif out_str:
                partial_output = out_str
            else:
                partial_output = err_str

            msg = (
                f"Command timed out after {ctx.command_timeout_sec}s and process tree was killed."
            )
            if partial_output:
                msg += f"\n\nPartial captured output:\n{_truncate_output(partial_output)}"

            return ToolResult(
                success=False,
                output=msg,
                error_code="TIMEOUT",
                execution_time_ms=elapsed_ms,
                metadata={"timeout_sec": ctx.command_timeout_sec, "pid": proc.pid},
            )

        finally:
            if job is not None:
                job.stop_and_verify()

    except (TaskCancelled, CleanupUnverified):
        raise
    except SecurityViolationError as e:
        return ToolResult(
            success=False,
            output=str(e),
            error_code="PERMISSION_DENIED",
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
            output=f"Error executing command: {e}",
            error_code="EXECUTION_ERROR",
            execution_time_ms=(time.perf_counter() - start_time) * 1000,
        )
