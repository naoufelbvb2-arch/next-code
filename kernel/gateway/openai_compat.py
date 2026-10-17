"""OpenAI-compatible LLM provider adapter supporting DeepSeek, OpenAI, vLLM, and Ollama."""

import asyncio
import json
import os
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from kernel.exceptions import ModelGatewayError, ModelOutputValidationError
from kernel.gateway.base import BaseLLMProvider
from kernel.schemas import Message, TokenUsage

T = TypeVar("T", bound=BaseModel)


class ProviderConfig(BaseModel):
    """Configuration for OpenAI-compatible LLM endpoint."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(
        default_factory=lambda: os.getenv(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"
        )
    )
    api_key: str | None = Field(default_factory=lambda: os.getenv("DEEPSEEK_API_KEY"))
    default_model: str = "deepseek-chat"
    timeout_sec: float = 60.0
    max_http_retries: int = 3


class OpenAICompatibleProvider(BaseLLMProvider):
    """Adapter for DeepSeek and OpenAI-compatible inference APIs."""

    def __init__(
        self,
        config: ProviderConfig | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config or ProviderConfig()
        self._custom_client = client
        self.base_url = self.config.base_url.rstrip("/")
        self.default_model = self.config.default_model

    def _get_client(self) -> httpx.AsyncClient:
        if self._custom_client is not None:
            return self._custom_client
        return httpx.AsyncClient(timeout=self.config.timeout_sec)

    async def _send_completion_request(
        self,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Send chat completion request with exponential backoff on transient HTTP errors."""
        headers = {
            "Content-Type": "application/json",
        }
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        url = f"{self.base_url}/chat/completions"
        client = self._get_client()
        owns_client = self._custom_client is None

        try:
            for attempt in range(self.config.max_http_retries):
                try:
                    resp = await client.post(url, json=payload, headers=headers)
                    if resp.status_code == 200:
                        return resp.json()

                    # Transient HTTP errors eligible for exponential backoff
                    if resp.status_code in (429, 502, 503, 504):
                        if attempt < self.config.max_http_retries - 1:
                            backoff = 0.5 * (2**attempt)
                            await asyncio.sleep(backoff)
                            continue

                    raise ModelGatewayError(
                        f"Inference API error {resp.status_code}: {resp.text}"
                    )
                except httpx.RequestError as exc:
                    if attempt < self.config.max_http_retries - 1:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue
                    raise ModelGatewayError(f"HTTP connection failed: {exc}") from exc

            raise ModelGatewayError("Exhausted HTTP retries without successful response.")
        finally:
            if owns_client:
                await client.aclose()

    async def generate_text(
        self,
        messages: list[Message],
        temperature: float = 0.0,
        max_tokens: int = 4096,
        task_id: str = "",
        model: str | None = None,
    ) -> tuple[str, TokenUsage]:
        """Generate text output and return latency and token usage metrics."""
        start_time = time.perf_counter()
        active_model = model or self.default_model

        payload: dict[str, Any] = {
            "model": active_model,
            "messages": [m.model_dump(exclude_none=True) for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        data = await self._send_completion_request(payload)
        elapsed_ms = (time.perf_counter() - start_time) * 1000

        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError) as e:
            raise ModelGatewayError(
                f"Malformed completion response from provider: {data}"
            ) from e

        usage_data = data.get("usage", {})
        prompt_tokens = usage_data.get("prompt_tokens", 0)
        completion_tokens = usage_data.get("completion_tokens", 0)
        total_tokens = usage_data.get(
            "total_tokens", prompt_tokens + completion_tokens
        )

        usage = TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=elapsed_ms,
            model_name=active_model,
            task_id=task_id,
        )
        return content, usage

    async def generate_structured(
        self,
        messages: list[Message],
        response_model: type[T],
        temperature: float = 0.0,
        max_retries: int = 2,
        task_id: str = "",
        model: str | None = None,
    ) -> tuple[T, TokenUsage]:
        """Generate structured data adhering to response_model schema with self-healing retry loop."""
        start_time = time.perf_counter()
        active_model = model or self.default_model

        schema_json = json.dumps(response_model.model_json_schema(), indent=2)
        schema_prompt = (
            f"\n\nCRITICAL: You MUST respond with a valid JSON object strictly conforming to this JSON Schema:\n"
            f"{schema_json}\n"
            f"Do not include explanations or markdown outside the valid JSON object."
        )

        curr_messages = [Message(role=m.role, content=m.content, name=m.name, tool_call_id=m.tool_call_id) for m in messages]

        # Inject schema prompt into system message if present, or add system message
        sys_idx = next(
            (i for i, m in enumerate(curr_messages) if m.role == "system"), None
        )
        if sys_idx is not None:
            updated_content = curr_messages[sys_idx].content + schema_prompt
            curr_messages[sys_idx] = Message(role="system", content=updated_content)
        else:
            curr_messages.insert(
                0,
                Message(
                    role="system",
                    content=f"You are a structured data generator.{schema_prompt}",
                ),
            )

        total_prompt_tokens = 0
        total_completion_tokens = 0
        last_raw_text = ""
        last_error_str = ""

        for attempt in range(max_retries + 1):
            raw_text, usage = await self.generate_text(
                messages=curr_messages,
                temperature=temperature,
                max_tokens=4096,
                task_id=task_id,
                model=active_model,
            )
            total_prompt_tokens += usage.prompt_tokens
            total_completion_tokens += usage.completion_tokens
            last_raw_text = raw_text

            # Clean potential markdown fences
            clean_text = raw_text.strip()
            if clean_text.startswith("```"):
                lines = clean_text.splitlines()
                if len(lines) >= 2 and lines[-1].strip().startswith("```"):
                    clean_text = "\n".join(lines[1:-1]).strip()
                    if clean_text.lower().startswith("json"):
                        clean_text = clean_text[4:].strip()

            try:
                parsed_model = response_model.model_validate_json(clean_text)
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                cumulative_usage = TokenUsage(
                    prompt_tokens=total_prompt_tokens,
                    completion_tokens=total_completion_tokens,
                    total_tokens=total_prompt_tokens + total_completion_tokens,
                    latency_ms=elapsed_ms,
                    model_name=active_model,
                    task_id=task_id,
                )
                return parsed_model, cumulative_usage
            except (ValidationError, json.JSONDecodeError, ValueError) as err:
                last_error_str = str(err)
                if attempt < max_retries:
                    # Self-healing feedback loop
                    curr_messages.append(
                        Message(role="assistant", content=raw_text)
                    )
                    curr_messages.append(
                        Message(
                            role="user",
                            content=(
                                f"Validation Error in your previous response: {last_error_str}\n"
                                f"Please correct the JSON output according to the schema:\n{schema_json}"
                            ),
                        )
                    )

        raise ModelOutputValidationError(
            message=f"Structured output failed schema validation after {max_retries} retries: {last_error_str}",
            raw_response=last_raw_text,
            validation_error=last_error_str,
        )
