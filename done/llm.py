"""Thin async OpenAI-compatible client for Crusoe: retries, model fallback, token accounting."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from .config import BASE_URL, Config, load_config


class BudgetExhausted(RuntimeError):
    """Raised before a call that the remaining budget cannot cover."""


@dataclass
class Budget:
    total: int
    used: int = 0
    cost_usd: float = 0.0
    by_model: dict[str, dict[str, float | int]] = field(default_factory=dict)

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.used)

    def spend(self, model: str, prompt: int, completion: int, cfg: Config) -> float:
        self.used += prompt + completion
        p_in, p_out = cfg.price_for(model)
        cost = (prompt / 1_000_000) * p_in + (completion / 1_000_000) * p_out
        self.cost_usd += cost
        slot = self.by_model.setdefault(model, {"tokens": 0, "cost_usd": 0.0, "calls": 0})
        slot["tokens"] += prompt + completion
        slot["cost_usd"] += cost
        slot["calls"] += 1
        return cost

    def naive_baseline_usd(self, root_model: str, cfg: Config) -> float:
        """What this same token count would have cost priced entirely at the root model."""
        p_in, p_out = cfg.price_for(root_model)
        # assume the observed 70/30 prompt/completion split, which is what we actually see
        return (self.used * 0.7 / 1_000_000) * p_in + (self.used * 0.3 / 1_000_000) * p_out


@dataclass
class LLMResult:
    content: str | None
    tool_calls: list[Any]
    usage: dict[str, int]
    model_used: str
    latency_ms: int

    def json(self) -> dict:
        return parse_json_lenient(self.content or "")


_client: AsyncOpenAI | None = None
_semaphore: asyncio.Semaphore | None = None
_consecutive_errors: dict[str, int] = {}


def client() -> AsyncOpenAI:
    global _client
    if _client is None:
        key = os.environ.get("CRUSOE_API_KEY")
        if not key:
            raise RuntimeError(
                "CRUSOE_API_KEY is not set. Copy .env.example to .env and put your key in it."
            )
        _client = AsyncOpenAI(api_key=key, base_url=BASE_URL, timeout=120.0, max_retries=0)
    return _client


def reset_client() -> None:
    """Tests and `done models` call this after mutating env."""
    global _client, _semaphore, _consecutive_errors
    _client = None
    _semaphore = None
    _consecutive_errors = {}


def semaphore(cfg: Config) -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(cfg.MAX_CONCURRENT_LLM)
    return _semaphore


async def list_models() -> list[dict]:
    resp = await client().models.list()
    return [m.model_dump() if hasattr(m, "model_dump") else dict(m) for m in resp.data]


async def chat(
    model: str,
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    json_mode: bool = False,
    max_tokens: int = 2000,
    temperature: float = 0.2,
    tag: str,
    ctx: Any = None,
) -> LLMResult:
    """One chat completion. Charges the run budget, traces the call, never dies on an outage."""
    cfg: Config = getattr(ctx, "cfg", None) or load_config()
    budget: Budget | None = getattr(ctx, "budget", None)
    trace = getattr(ctx, "trace", None)
    node_id = getattr(ctx, "_current_node_id", None)

    if budget is not None and budget.remaining < max_tokens:
        raise BudgetExhausted(
            f"budget has {budget.remaining} tokens left, call reserves {max_tokens}"
        )

    client()  # fail fast and loudly on a missing key instead of retrying it six times

    attempt_models = [model]
    if cfg.fallback_model != model:
        attempt_models.append(cfg.fallback_model)

    last_err: Exception | None = None
    for use_model in attempt_models:
        if _consecutive_errors.get(use_model, 0) >= 2 and use_model != cfg.fallback_model:
            if trace:
                trace.event("model_fallback", node_id, from_model=use_model,
                            to_model=cfg.fallback_model, reason="2 consecutive errors")
            continue
        for attempt in range(cfg.RETRIES):
            kwargs: dict[str, Any] = {
                "model": use_model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            if json_mode and not tools:
                kwargs["response_format"] = {"type": "json_object"}
            started = time.time()
            try:
                async with semaphore(cfg):
                    resp = await client().chat.completions.create(**kwargs)
            except Exception as exc:  # noqa: BLE001 - any API failure is retryable here
                last_err = exc
                _consecutive_errors[use_model] = _consecutive_errors.get(use_model, 0) + 1
                if trace:
                    trace.event("llm_error", node_id, model=use_model, tag=tag,
                                attempt=attempt + 1, error=str(exc)[:300])
                if json_mode and "response_format" in str(exc):
                    json_mode = False  # model doesn't support it; prompt already demands JSON
                await asyncio.sleep(min(8.0, 1.5 ** attempt))
                continue

            _consecutive_errors[use_model] = 0
            latency_ms = int((time.time() - started) * 1000)
            usage = {
                "prompt": getattr(resp.usage, "prompt_tokens", 0) or 0,
                "completion": getattr(resp.usage, "completion_tokens", 0) or 0,
                "total": getattr(resp.usage, "total_tokens", 0) or 0,
            }
            choice = resp.choices[0].message
            cost = 0.0
            if budget is not None:
                cost = budget.spend(use_model, usage["prompt"], usage["completion"], cfg)
            if trace:
                trace.llm_call(tag=tag, model=use_model, usage=usage, latency_ms=latency_ms,
                               node_id=node_id, cost_usd=round(cost, 6))
            if use_model != model and trace:
                trace.event("model_fallback", node_id, from_model=model, to_model=use_model,
                            reason="primary model unavailable")
            return LLMResult(
                content=choice.content,
                tool_calls=list(choice.tool_calls or []),
                usage=usage,
                model_used=use_model,
                latency_ms=latency_ms,
            )

    raise RuntimeError(f"all models failed for tag={tag}: {last_err}")


# --- lenient JSON -----------------------------------------------------------
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_json_lenient(text: str) -> dict:
    """Not every model honors response_format. Dig the first JSON object out of whatever came back."""
    if not text:
        return {}
    for candidate in _json_candidates(text):
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
        if isinstance(value, list):
            return {"items": value}
    return {}


def _json_candidates(text: str):
    text = text.strip()
    yield text
    for m in _FENCE.finditer(text):
        yield m.group(1).strip()
    # first balanced {...} block
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                yield text[start : i + 1]
                start = -1
