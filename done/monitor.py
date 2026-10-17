"""Loop and drift detection. Cheap deterministic heuristics first; the LLM drift judge only
fires when a heuristic says something smells. Every flag carries its evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Literal

from .models import MonitorFlag, Node
from .prompts import render

Flag = Literal["OK", "LOOP", "DRIFT", "BUDGET"]


@dataclass
class _State:
    hashes: list[str] = field(default_factory=list)
    recent: list[str] = field(default_factory=list)      # human-readable step lines
    steps: int = 0
    steps_since_write: int = 0
    last_pass_count: int | None = None
    steps_since_pass_change: int = 0
    last_drift_step: int = 0
    drift_hits: int = 0
    impossible: bool = False
    impossible_reason: str = ""


def _states(ctx) -> dict[str, _State]:
    if not hasattr(ctx, "_monitor_states"):
        ctx._monitor_states = {}
    return ctx._monitor_states


def state_for(node: Node, ctx) -> _State:
    return _states(ctx).setdefault(node.id, _State())


def reset(ctx) -> None:
    ctx._monitor_states = {}


def fingerprint(tool_name: str, args: dict) -> str:
    """Same tool + same arguments = same action, regardless of key order or whitespace."""
    canon = json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha1(f"{tool_name}|{canon}".encode()).hexdigest()[:12]


async def step(node: Node, tool_call: dict, ctx) -> Flag:
    """Called after every worker tool call. tool_call = {"name":..., "args":..., "result":...}."""
    st = state_for(node, ctx)
    name = tool_call.get("name", "?")
    args = tool_call.get("args") or {}
    result = str(tool_call.get("result") or "")

    st.steps += 1
    h = fingerprint(name, args)
    st.hashes.append(h)
    st.recent.append(_describe(name, args, result))
    st.recent = st.recent[-8:]

    # --- progress tracking --------------------------------------------------
    if name == "write_file":
        st.steps_since_write = 0
    else:
        st.steps_since_write += 1

    passes = _pass_count(name, result)
    if passes is not None:
        if passes == st.last_pass_count:
            st.steps_since_pass_change += 1
        else:
            # The world changed, so repetition up to now was not a loop: re-running the checks
            # after a fix is exactly the behaviour we ask for. Clearing the history stops the
            # exact-loop rule firing on the very call that reached a passing state.
            improved = st.last_pass_count is not None and passes > st.last_pass_count
            st.steps_since_pass_change = 0
            st.last_pass_count = passes
            if improved:
                st.hashes = [h]
                ctx.trace.event("loop_suspicion_cleared", node.id, passes=passes,
                                reason="check pass count improved")

    # --- 1. exact loop ------------------------------------------------------
    repeats = st.hashes.count(h)
    if repeats >= ctx.cfg.LOOP_REPEAT_THRESHOLD:
        return _flag(node, ctx, st, "LOOP",
                     f"identical action repeated {repeats}x: {name}({_short(args)}) [hash {h}]")

    # --- 2. oscillation -----------------------------------------------------
    if (period := _oscillation(st.hashes)) is not None:
        return _flag(node, ctx, st, "LOOP",
                     f"oscillating with period {period}: {' → '.join(st.hashes[-6:])}")

    # --- 3. no progress -----------------------------------------------------
    if (st.steps_since_write >= ctx.cfg.NO_PROGRESS_STEPS
            and st.steps_since_pass_change >= ctx.cfg.NO_PROGRESS_STEPS):
        return _flag(node, ctx, st, "LOOP",
                     f"{st.steps_since_write} steps with no file written and no change in the "
                     f"check pass count (stuck at {st.last_pass_count})")

    # --- 4. budget ----------------------------------------------------------
    if node.budget_tokens and node.tokens_used > node.budget_tokens * ctx.cfg.BUDGET_WARN_FRACTION:
        return _flag(node, ctx, st, "BUDGET",
                     f"used {node.tokens_used}/{node.budget_tokens} tokens of its allocation")

    # --- 5. drift (LLM, only on trigger) ------------------------------------
    off_goal_write = name == "write_file" and _unexpected_path(args.get("path", ""), node)
    due = st.steps - st.last_drift_step >= ctx.cfg.DRIFT_EVERY_N_STEPS
    if off_goal_write or due:
        st.last_drift_step = st.steps
        verdict = await _drift_judge(node, st, ctx,
                                     trigger="off_goal_write" if off_goal_write else "periodic")
        if verdict == "DRIFT":
            st.drift_hits += 1
            return _flag(node, ctx, st, "DRIFT",
                         f"drift judge flagged node (hit {st.drift_hits}): {st.impossible_reason}")
    return "OK"


async def _drift_judge(node: Node, st: _State, ctx, *, trigger: str) -> Flag:
    from . import llm

    prompt = render(
        "drift_judge",
        root_goal=node.root_goal,
        goal=node.goal,
        recent_steps="\n".join(f"{i + 1}. {line}" for i, line in enumerate(st.recent)),
    )
    try:
        res = await llm.chat(
            ctx.cfg.drift_judge_model,
            [{"role": "user", "content": prompt}],
            json_mode=True, max_tokens=300, temperature=0.0, tag="drift_judge", ctx=ctx,
        )
    except Exception as exc:  # noqa: BLE001 - the monitor must never be what breaks a run
        ctx.trace.event("drift_judge_error", node.id, error=str(exc)[:200])
        return "OK"
    data = res.json()
    try:
        score = int(float(data.get("drift", 0)))
    except (TypeError, ValueError):
        score = 0
    reason = str(data.get("reason", ""))[:300]
    if data.get("impossible"):
        st.impossible = True
        st.impossible_reason = reason
        node.log(f"drift judge: goal may be impossible — {reason}")
        ctx.trace.event("impossible_signal", node.id, reason=reason, model=res.model_used)
    ctx.trace.event("drift_judge", node.id, drift=score, reason=reason, trigger=trigger,
                    model=res.model_used, impossible=bool(data.get("impossible")))
    if score >= ctx.cfg.DRIFT_THRESHOLD:
        st.impossible_reason = reason or st.impossible_reason
        return "DRIFT"
    return "OK"


def _flag(node: Node, ctx, st: _State, kind: str, evidence: str) -> Flag:
    node.flags.append(MonitorFlag(kind=kind, evidence=evidence, step=st.steps))  # type: ignore[arg-type]
    node.log(f"monitor: {kind} — {evidence[:140]}")
    ctx.trace.event("monitor_flag", node.id, kind=kind, evidence=evidence, step=st.steps)
    ctx.bump(f"monitor_{kind.lower()}")
    return kind  # type: ignore[return-value]


def _oscillation(hashes: list[str]) -> int | None:
    """Last 6 actions repeating with period 2 or 3 (A B A B A B / A B C A B C)."""
    if len(hashes) < 6:
        return None
    window = hashes[-6:]
    for period in (2, 3):
        if len(set(window)) <= 1:
            continue  # that is the exact-loop case, already handled
        if all(window[i] == window[i - period] for i in range(period, 6)):
            return period
    return None


def _pass_count(name: str, result: str) -> int | None:
    if name != "run_checks":
        return None
    try:
        data = json.loads(result)
        return int(data.get("passed", 0))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _unexpected_path(path: str, node: Node) -> bool:
    """A write nobody asked for: not named by a check, not a test, not a readme."""
    if not path or not node.dod:
        return False
    low = path.lower()
    if any(part in low for part in ("test", "readme", "__init__", "conftest", "pyproject")):
        return False
    haystack = " ".join(
        f"{c.command or ''} {c.path or ''} {c.description or ''}" for c in node.dod
    )
    stem = path.rsplit("/", 1)[-1]
    return stem not in haystack and path not in haystack


def _describe(name: str, args: dict, result: str) -> str:
    if name == "write_file":
        return f"write_file {args.get('path', '?')} ({len(str(args.get('content', '')))} bytes)"
    if name == "run_shell":
        return f"run_shell {str(args.get('cmd', ''))[:80]} → {result[:60].replace(chr(10), ' ')}"
    if name == "run_checks":
        return f"run_checks → {result[:80].replace(chr(10), ' ')}"
    if name == "read_file":
        return f"read_file {args.get('path', '?')}"
    return f"{name} {_short(args)}"


def _short(args: dict) -> str:
    return json.dumps(args, default=str)[:80]
