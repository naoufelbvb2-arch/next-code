"""ToolRegistry and execution dispatcher for deterministic L1 tools."""

import time
from typing import Any, Callable

from kernel.schemas import ExecutionContext, ToolResult
from kernel.tools.filesystem import apply_patch, list_dir, read_file
from kernel.tools.search import find_files, grep_search
from kernel.tools.shell import run_command


class ToolRegistry:
    """Central deterministic registry and dispatcher for sandboxed tools."""

    def __init__(self) -> None:
        self._tools: dict[str, Callable[..., ToolResult]] = {}
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        self.register("read_file", read_file)
        self.register("list_dir", list_dir)
        self.register("apply_patch", apply_patch)
        self.register("grep_search", grep_search)
        self.register("find_files", find_files)
        self.register("run_command", run_command)

    def register(self, name: str, tool_fn: Callable[..., ToolResult]) -> None:
        """Register a tool callable under a unique name."""
        self._tools[name] = tool_fn

    def get_tool(self, name: str) -> Callable[..., ToolResult] | None:
        """Retrieve tool callable by name."""
        return self._tools.get(name)

    def list_tools(self) -> list[str]:
        """List all registered tool names."""
        return sorted(self._tools.keys())

    def execute(
        self,
        name: str,
        ctx: ExecutionContext,
        **kwargs: Any,
    ) -> ToolResult:
        """Dispatch execution to the registered tool with deterministic error capture."""
        tool_fn = self.get_tool(name)
        if not tool_fn:
            return ToolResult(
                success=False,
                output=f"Unknown tool '{name}'. Registered tools: {self.list_tools()}",
                error_code="EXECUTION_ERROR",
                execution_time_ms=0.0,
            )

        start = time.perf_counter()
        try:
            return tool_fn(ctx, **kwargs)
        except Exception as e:
            return ToolResult(
                success=False,
                output=f"Dispatcher unhandled exception in '{name}': {e}",
                error_code="EXECUTION_ERROR",
                execution_time_ms=(time.perf_counter() - start) * 1000,
            )
