"""ModelGateway for routing ModelProfile requests and accounting token usage in TokenLedger."""

from typing import TypeVar

from pydantic import BaseModel

from kernel.config import KernelConfig
from kernel.exceptions import ModelGatewayError
from kernel.gateway.base import BaseLLMProvider
from kernel.gateway.ledger import TokenLedger
from kernel.gateway.openai_compat import OpenAICompatibleProvider, ProviderConfig
from kernel.schemas import Message, ModelProfile, TokenUsage

T = TypeVar("T", bound=BaseModel)


class ModelGateway:
    """Central gateway routing model requests based on ModelProfile and recording metrics in TokenLedger."""

    def __init__(
        self,
        config: KernelConfig | None = None,
        ledger: TokenLedger | None = None,
    ) -> None:
        self.config = config or KernelConfig()
        self.ledger = ledger or TokenLedger()
        self._routes: dict[ModelProfile, tuple[BaseLLMProvider, str]] = {}
        self._init_default_routes()

    def _init_default_routes(self) -> None:
        """Initialize default routes for DeepSeek reasoning and worker profiles."""
        deepseek_config = ProviderConfig(
            base_url=self.config.deepseek_base_url,
            api_key=self.config.deepseek_api_key,
            default_model="deepseek-chat",
            timeout_sec=float(self.config.default_command_timeout_sec * 2),
        )
        default_provider = OpenAICompatibleProvider(config=deepseek_config)

        self.register_route(
            ModelProfile.REASONING,
            default_provider,
            model_name="deepseek-reasoner",
        )
        self.register_route(
            ModelProfile.FAST_WORKER,
            default_provider,
            model_name="deepseek-chat",
        )
        self.register_route(
            ModelProfile.LOCAL_CODER,
            default_provider,
            model_name="deepseek-chat",
        )

    def register_route(
        self,
        profile: ModelProfile,
        provider: BaseLLMProvider,
        model_name: str = "deepseek-chat",
    ) -> None:
        """Register a provider and model target for a specific profile."""
        self._routes[profile] = (provider, model_name)

    async def generate_text(
        self,
        profile: ModelProfile,
        messages: list[Message],
        task_id: str,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> tuple[str, TokenUsage]:
        """Route text completion request and record usage in TokenLedger."""
        if profile not in self._routes:
            raise ModelGatewayError(f"No provider registered for profile: {profile}")

        provider, model_name = self._routes[profile]
        if hasattr(provider, "generate_text") and "model" in provider.generate_text.__code__.co_varnames:
            text, usage = await provider.generate_text(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                task_id=task_id,
                model=model_name,
            )
        else:
            text, usage = await provider.generate_text(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                task_id=task_id,
            )

        self.ledger.record(
            usage=usage,
            profile=profile,
            max_tokens_budget=self.config.max_tokens_per_task,
        )
        return text, usage

    async def generate_structured(
        self,
        profile: ModelProfile,
        messages: list[Message],
        response_model: type[T],
        task_id: str,
        temperature: float = 0.0,
        max_retries: int | None = None,
    ) -> tuple[T, TokenUsage]:
        """Route structured generation request with validation and record usage in TokenLedger."""
        if profile not in self._routes:
            raise ModelGatewayError(f"No provider registered for profile: {profile}")

        provider, model_name = self._routes[profile]
        retries = max_retries if max_retries is not None else self.config.max_retries

        if hasattr(provider, "generate_structured") and "model" in provider.generate_structured.__code__.co_varnames:
            structured_data, usage = await provider.generate_structured(
                messages=messages,
                response_model=response_model,
                temperature=temperature,
                max_retries=retries,
                task_id=task_id,
                model=model_name,
            )
        else:
            structured_data, usage = await provider.generate_structured(
                messages=messages,
                response_model=response_model,
                temperature=temperature,
                max_retries=retries,
                task_id=task_id,
            )

        self.ledger.record(
            usage=usage,
            profile=profile,
            max_tokens_budget=self.config.max_tokens_per_task,
        )
        return structured_data, usage
