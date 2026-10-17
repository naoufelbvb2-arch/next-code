"""Deterministic kernel exceptions for Layer 1."""

from typing import Any


class KernelError(Exception):
    """Base exception for all Layer 1 deterministic kernel errors."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class SecurityViolationError(KernelError):
    """Raised when an operation attempts to breach workspace isolation or blacklisted paths."""


class PermissionDeniedError(KernelError):
    """Raised when an operation violates role-based access control or file-scope contracts."""


class SyntaxCheckError(KernelError):
    """Raised when a pre-mutation syntax check (AST/JSON) fails."""

    def __init__(
        self,
        message: str,
        filename: str | None = None,
        lineno: int | None = None,
        offset: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details)
        self.filename = filename
        self.lineno = lineno
        self.offset = offset


class PatchConflictError(KernelError):
    """Raised when a patch block cannot be applied unambiguously."""


class CommandExecutionError(KernelError):
    """Raised when shell command execution fails."""


class CommandTimeoutError(CommandExecutionError):
    """Raised when a command exceeds its allotted timeout and process tree is terminated."""


class ModelGatewayError(KernelError):
    """Base exception for Model Gateway operations."""


class ModelOutputValidationError(ModelGatewayError):
    """Raised when model structured output fails validation after self-healing retries."""

    def __init__(
        self,
        message: str,
        raw_response: str,
        validation_error: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details)
        self.raw_response = raw_response
        self.validation_error = validation_error


class BudgetExceededError(ModelGatewayError):
    """Raised when a task exceeds its allocated token budget cap."""
