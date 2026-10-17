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


# Verification is the one thing a run may not run out of money for: an unjudged node is
# indistinguishable from a lying one. These tags may dip into the reserve; nothing else may.
VERIFY_TAGS = {"verifier", "meta_verifier"}


@dataclass
class Budget:
    total: int
    used: int = 0
    cost_usd: float = 0.0
    verify_reserve_fraction: float = 0.12
    min_verify_reserve: int = 6_000
    by_model: dict[str, dict[str, float | int]] = field(default_factory=dict)

    @property
    def verify_reserve(self) -> int:
        """Enough for BOTH verification calls (meta-verify, then adjudicate) with their
        prompts, never more than 40% of the run."""
        floor = min(self.min_verify_reserve, int(self.total * 0.40))
        return max(int(self.total * self.verify_reserve_fraction), floor)

    @property
    def remaining(self) -> int:
        """What work may spend: the reserve is invisible to everything but verification."""
        return max(0, self.total - self.used - self.verify_reserve)

    def remaining_for(self, tag: str) -> int:
        if tag in VERIFY_TAGS:
            return max(0, self.total - self.used)
        return self.remaining

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


def charge(ctx: Any, model: str, usage: dict) -> float:
    """Bill one call to the run AND to the node that made it.

    Per-node accuracy is what makes the worker's ceiling and the monitor's BUDGET flag mean
    anything; the node is resolved from a ContextVar so concurrent siblings cannot
    cross-charge each other. Shared by the real client and the test double.
    """
    budget: Budget | None = getattr(ctx, "budget", None)
    if budget is None:
        return 0.0
    cfg: Config = getattr(ctx, "cfg", None) or load_config()
    prompt, completion = usage.get("prompt", 0), usage.get("completion", 0)
    cost = budget.spend(model, prompt, completion, cfg)
    node = (getattr(ctx, "nodes", None) or {}).get(getattr(ctx, "_current_node_id", None))
    if node is not None:
        node.tokens_used += usage.get("total") or (prompt + completion)
        node.cost_usd += cost
    return cost


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
    fallback: str | list[str] | None = None,
) -> LLMResult:
    """One chat completion. Charges the run budget, traces the call, never dies on an outage."""
    cfg: Config = getattr(ctx, "cfg", None) or load_config()
    budget: Budget | None = getattr(ctx, "budget", None)
    trace = getattr(ctx, "trace", None)
    node_id = getattr(ctx, "_current_node_id", None)

    if budget is not None and budget.remaining_for(tag) < max_tokens:
        raise BudgetExhausted(
            f"budget has {budget.remaining_for(tag)} tokens left for {tag!r}, "
            f"call reserves {max_tokens}"
        )

    client()  # fail fast and loudly on a missing key instead of retrying it six times

    # An explicit `fallback` chain matters for verification: a replacement model from the
    # worker's own family would void the cross-family independence guarantee.
    chain = ([fallback] if isinstance(fallback, str) else list(fallback or [])) or [cfg.fallback_model]
    attempt_models = [model] + [m for m in chain if m != model]

    last_err: Exception | None = None
    for use_model in attempt_models:
        if _consecutive_errors.get(use_model, 0) >= 2 and use_model != attempt_models[-1]:
            if trace:
                trace.event("model_fallback", node_id, from_model=use_model,
                            to_model=attempt_models[-1], reason="2 consecutive errors")
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
            cost = charge(ctx, use_model, usage)
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
    return _repair_truncated(text)


def _repair_truncated(text: str) -> dict:
    """A model that hits max_tokens leaves an unclosed object. Close it and salvage the
    fields that did arrive, rather than discarding the whole ruling."""
    start = text.find("{")
    if start < 0:
        return {}
    body = text[start:]
    for trim in range(0, min(len(body), 400)):
        candidate = body[: len(body) - trim] if trim else body
        # drop a dangling partial token, then close what is still open
        candidate = candidate.rstrip().rstrip(",")
        opens = candidate.count("{") - candidate.count("}")
        brackets = candidate.count("[") - candidate.count("]")
        if candidate.count('"') % 2:
            candidate += '"'
        candidate += "]" * max(0, brackets) + "}" * max(0, opens)
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
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
