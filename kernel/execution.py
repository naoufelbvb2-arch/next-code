"""Optional, task-local execution observation and cooperative cancellation.

No global event bus. Cancellation is confirmed only after awaited work unwinds;
blocking tools are drained, never abandoned behind a cancelled Future.
"""
import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from architect.contracts import TaskOutcome


EXECUTION_CONTROL_VERSION = 1


class TaskCancelled(Exception):
    """Engine stopped cooperatively; prior workspace writes are not rolled back."""


class CleanupUnverified(Exception):
    """Owned tool cleanup could not be established. No stop confirmation."""


class CancellationControl:
    def __init__(self):
        self._requested = Event()

    def request(self):
        self._requested.set()

    @property
    def requested(self):
        return self._requested.is_set()

    def check(self):
        if self.requested:
            raise TaskCancelled("Execution stopped at an engine cancellation boundary.")


class ExecutionObserver(Protocol):
    def started(self, task_id: str, workspace_root: Path) -> None: ...
    def outcome(self, outcome: "TaskOutcome") -> None: ...
    def cancelled(self, task_id: str, workspace_root: Path) -> None: ...
    def failed(self, task_id: str, workspace_root: Path, error: BaseException) -> None: ...


@dataclass(frozen=True)
class ExecutionBinding:
    control: CancellationControl | None = None
    observer: ExecutionObserver | None = None


binding: ContextVar[ExecutionBinding] = ContextVar("agent_os_execution", default=ExecutionBinding())


def check_cancelled():
    control = binding.get().control
    if control is not None:
        control.check()


async def gateway_wait(awaitable):
    """Interrupt an awaited Gateway and finish its finally blocks before returning."""
    control = binding.get().control
    if control is None:
        return await awaitable
    task = asyncio.ensure_future(awaitable)
    try:
        while not task.done():
            if control.requested:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                control.check()
            await asyncio.wait({task}, timeout=0.025)
        result = await task
        control.check()
        return result
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


async def tool_wait(fn, *args):
    """With control, move blocking tool work off the reader's event loop and drain it."""
    if binding.get().control is None:
        return fn(*args)
    check_cancelled()
    task = asyncio.create_task(asyncio.to_thread(fn, *args))
    try:
        result = await asyncio.shield(task)
    except asyncio.CancelledError:
        # External cancellation is not cooperative confirmation; never abandon
        # the tool thread. Desktop's independent Job remains the final fallback.
        await task
        raise
    check_cancelled()
    return result
