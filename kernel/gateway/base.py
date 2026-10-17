"""Abstract Base Class for LLM providers."""

from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel

from kernel.schemas import Message, TokenUsage

T = TypeVar("T", bound=BaseModel)


class BaseLLMProvider(ABC):
    """Abstract interface for LLM provider implementations."""

    @abstractmethod
    async def generate_text(
        self,
        messages: list[Message],
        temperature: float = 0.0,
        max_tokens: int = 4096,
        task_id: str = "",
    ) -> tuple[str, TokenUsage]:
        """Generate unstructured text from messages."""
        ...

    @abstractmethod
    async def generate_structured(
        self,
        messages: list[Message],
        response_model: type[T],
        temperature: float = 0.0,
        max_retries: int = 2,
        task_id: str = "",
    ) -> tuple[T, TokenUsage]:
        """Generate structured data strictly conforming to response_model schema with self-healing retries."""
        ...
