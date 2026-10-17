"""Token accounting and budget cap enforcement."""

import json
import threading
from pathlib import Path
from typing import Any

from kernel.exceptions import BudgetExceededError
from kernel.schemas import ModelProfile, TokenUsage


class TokenLedger:
    """Thread-safe and async-safe token usage accounting ledger with optional JSONL persistence."""

    def __init__(self, log_path: Path | str | None = None) -> None:
        self.log_path = Path(log_path).resolve() if log_path else None
        self._lock = threading.Lock()
        self._records: list[dict[str, Any]] = []

    def record(
        self,
        usage: TokenUsage,
        profile: ModelProfile | str | None = None,
        max_tokens_budget: int | None = None,
    ) -> None:
        """Record a TokenUsage event and verify task budget limits."""
        with self._lock:
            # Check budget before or after recording
            if max_tokens_budget is not None:
                current_total = sum(
                    r["usage"].total_tokens
                    for r in self._records
                    if r["usage"].task_id == usage.task_id
                )
                if current_total + usage.total_tokens > max_tokens_budget:
                    raise BudgetExceededError(
                        f"Task '{usage.task_id}' exceeded token budget limit: {current_total + usage.total_tokens} > {max_tokens_budget}"
                    )

            entry = {
                "task_id": usage.task_id,
                "profile": profile.value if isinstance(profile, ModelProfile) else profile,
                "model_name": usage.model_name,
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
                "latency_ms": usage.latency_ms,
                "usage": usage,
            }
            self._records.append(entry)

            if self.log_path:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                jsonl_payload = {
                    "task_id": usage.task_id,
                    "profile": entry["profile"],
                    "model_name": usage.model_name,
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                    "total_tokens": usage.total_tokens,
                    "latency_ms": usage.latency_ms,
                }
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(jsonl_payload) + "\n")

    def get_task_summary(self, task_id: str) -> dict[str, Any]:
        """Aggregate total token usage and latency for a given task_id."""
        with self._lock:
            task_records = [r for r in self._records if r["task_id"] == task_id]
            prompt_tokens = sum(r["prompt_tokens"] for r in task_records)
            completion_tokens = sum(r["completion_tokens"] for r in task_records)
            total_tokens = sum(r["total_tokens"] for r in task_records)
            total_latency = sum(r["latency_ms"] for r in task_records)

            models_used: dict[str, int] = {}
            for r in task_records:
                m = r["model_name"]
                models_used[m] = models_used.get(m, 0) + r["total_tokens"]

            return {
                "task_id": task_id,
                "call_count": len(task_records),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "total_latency_ms": round(total_latency, 2),
                "model_breakdown": models_used,
            }

    def enforce_budget(self, task_id: str, max_tokens: int) -> None:
        """Raise BudgetExceededError if task usage exceeds max_tokens."""
        summary = self.get_task_summary(task_id)
        if summary["total_tokens"] > max_tokens:
            raise BudgetExceededError(
                f"Task '{task_id}' has consumed {summary['total_tokens']} tokens, exceeding budget cap of {max_tokens}."
            )

    def reset(self) -> None:
        """Clear all in-memory records."""
        with self._lock:
            self._records.clear()
