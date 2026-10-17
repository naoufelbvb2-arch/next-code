"""WorkspaceGuard for enforcing path jail, blacklist rules, and RBAC."""

import fnmatch
import re
from pathlib import Path

from kernel.config import DEFAULT_BLACKLIST_PATTERNS
from kernel.exceptions import PermissionDeniedError, SecurityViolationError
from kernel.schemas import AgentRole, ExecutionContext


CATASTROPHIC_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"rm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+/\s*$", re.IGNORECASE),
    re.compile(r"rm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+/\w+", re.IGNORECASE),
    re.compile(r"rm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+\*", re.IGNORECASE),
    re.compile(r"git\s+reset\s+--hard", re.IGNORECASE),
    re.compile(r"git\s+clean\s+-[a-zA-Z]*f", re.IGNORECASE),
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", re.IGNORECASE),
    re.compile(r"(>\s*/dev/sd[a-z]|mkfs\.|dd\s+if=)", re.IGNORECASE),
    re.compile(r"(\brm\b|\bdel\b|\brmdir\b|\bmv\b|\bmove\b).*(\.git\b|\.git/|/\.git)", re.IGNORECASE),
]

SCOUT_FORBIDDEN_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(rm|del|rmdir|erase|unlink|mv|move|cp|copy|truncate|chmod|chown)\b", re.IGNORECASE),
    re.compile(r"\bgit\s+(commit|push|merge|rebase|reset|clean|checkout|branch\s+-[dD]|tag\s+-d)\b", re.IGNORECASE),
    re.compile(r"(>>?|\|)\s*[\w./\\-]+", re.IGNORECASE),
]


class WorkspaceGuard:
    """Deterministic security boundary and RBAC enforcer."""

    def __init__(
        self,
        workspace_root: Path,
        blacklist_patterns: list[str] | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve()
        self.blacklist_patterns = (
            list(blacklist_patterns)
            if blacklist_patterns is not None
            else list(DEFAULT_BLACKLIST_PATTERNS)
        )

    def _is_blacklisted(self, path: Path) -> str | None:
        """Check if path matches any blacklist pattern (case-insensitive for safety)."""
        try:
            rel = path.resolve().relative_to(self.workspace_root)
        except ValueError:
            return "outside_workspace"

        if rel == Path("."):
            return None

        rel_str = rel.as_posix().lower()
        rel_name = rel.name.lower()
        parts = [p.lower() for p in rel.parts]

        for raw_pattern in self.blacklist_patterns:
            pattern = raw_pattern.lower()
            if fnmatch.fnmatch(rel_str, pattern):
                return raw_pattern
            if fnmatch.fnmatch(rel_name, pattern):
                return raw_pattern
            if pattern.endswith("/**"):
                prefix = pattern[:-3]
                if prefix in parts or rel_str == prefix or rel_str.startswith(f"{prefix}/"):
                    return raw_pattern
            if pattern in parts:
                return raw_pattern
            try:
                if Path(rel_str).match(pattern):
                    return raw_pattern
            except ValueError:
                pass

        return None

    def resolve_safe_path(self, rel_or_abs_path: str | Path) -> Path:
        """Resolve path, ensure zero path escape, and verify blacklist boundaries."""
        input_path = Path(rel_or_abs_path)
        if not input_path.is_absolute():
            candidate = (self.workspace_root / input_path).resolve()
        else:
            candidate = input_path.resolve()

        # Zero Path Escape: check relative to workspace_root
        try:
            candidate.relative_to(self.workspace_root)
        except ValueError:
            raise SecurityViolationError(
                f"Path escape detected: '{rel_or_abs_path}' resolves to '{candidate}' outside workspace '{self.workspace_root}'"
            )

        # Check blacklist patterns
        violation = self._is_blacklisted(candidate)
        if violation is not None:
            raise SecurityViolationError(
                f"Access to path '{rel_or_abs_path}' (resolved: '{candidate}') is forbidden by blacklist rule '{violation}'."
            )

        return candidate

    def authorize_read(self, ctx: ExecutionContext, target_path: str | Path) -> Path:
        """Authorize read operation for any valid execution context role."""
        return self.resolve_safe_path(target_path)

    def authorize_write(self, ctx: ExecutionContext, target_path: str | Path) -> Path:
        """Authorize file mutation strictly for SURGEON role within allowed_files contract."""
        safe_path = self.resolve_safe_path(target_path)

        if ctx.role != AgentRole.SURGEON:
            raise PermissionDeniedError(
                f"Role '{ctx.role.value}' is not authorized to mutate files. Only SURGEON may mutate files."
            )

        rel = safe_path.relative_to(self.workspace_root).as_posix()
        normalized_allowed = {Path(p).as_posix() for p in ctx.allowed_files}

        if rel not in normalized_allowed:
            raise PermissionDeniedError(
                f"File '{rel}' is not in allowed_files contract: {sorted(normalized_allowed)}"
            )

        return safe_path

    def authorize_command(self, ctx: ExecutionContext, command: str) -> None:
        """Authorize shell command execution against RBAC and catastrophic command rules."""
        cmd_stripped = command.strip()

        # Catastrophic command check for all roles
        for pat in CATASTROPHIC_PATTERNS:
            if pat.search(cmd_stripped):
                raise SecurityViolationError(
                    f"Command '{command}' matches forbidden catastrophic pattern: {pat.pattern}"
                )

        # SCOUT role restrictions
        if ctx.role == AgentRole.SCOUT:
            for pat in SCOUT_FORBIDDEN_PATTERNS:
                if pat.search(cmd_stripped):
                    raise PermissionDeniedError(
                        f"SCOUT role is read-only and cannot execute mutating/destructive command: '{command}'"
                    )
