"""The leaf worker: a monitored tool loop that may not claim done until its own checks pass."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import llm, monitor, tools
from .checks import run_checks
from .dod_writer import render_checks
from .models import Node
from .prompts import node_vars, render


@dataclass
class WorkerOutcome:
    status: str = "blocked"           # "done" | "blocked"
    summary: str = ""
    needs_human: bool = False
    human_question: str | None = None
    interrupted_by: str | None = None  # "LOOP" | "DRIFT" | "BUDGET" | "STEPS" | "BUDGET_EXHAUSTED"
    steps: int = 0
    claimed_done: bool = False         # what it *said*; the verifier decides what is true
    notes: list[str] = field(default_factory=list)


async def execute(node: Node, ctx, *, attempt: int = 1,
                  failing: str = "", findings: str = "") -> WorkerOutcome:
    node.status = "executing"
    ctx.trace.node_update(node)

    retry_block = ""
    if attempt > 1:
        retry_block = render("worker_retry", attempt=attempt,
                             failing=failing or "(the checks below were failing)",
                             findings=findings or "(no findings recorded)")

    system = render(
        "worker",
        **node_vars(node),
        checks=render_checks(node.dod),
        retry_block=retry_block,
    )
    messages: list[dict] = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Begin. Your goal: {node.goal}"},
    ]

    out = WorkerOutcome()
    ceiling = int(node.budget_tokens * ctx.cfg.WORKER_SHARE) if node.budget_tokens else 0
    for step in range(ctx.cfg.MAX_TOOL_STEPS_PER_LEAF):
        if ceiling and node.tokens_used >= ceiling:
            out.interrupted_by = "BUDGET"
            node.log(f"worker hit its share of the node budget ({node.tokens_used}/{ceiling}); "
                     f"the rest is reserved for verification")
            break
        messages = _trim(messages)
        try:
            res = await llm.chat(
                node.model, messages, tools=tools.TOOL_SCHEMAS,
                max_tokens=2600, tag="worker", ctx=ctx,
            )
        except llm.BudgetExhausted as exc:
            out.interrupted_by = "BUDGET_EXHAUSTED"
            out.notes.append(str(exc))
            node.log("worker stopped: node budget exhausted")
            break

        if not res.tool_calls:
            content = (res.content or "").strip()
            messages.append({"role": "assistant", "content": content or "(no content)"})
            if out.notes.count("nudged") >= 1:
                out.notes.append("worker stopped calling tools")
                node.log("worker stopped calling tools; ending attempt")
                break
            out.notes.append("nudged")
            messages.append({
                "role": "user",
                "content": "Use the tools. Write the files with write_file, then call run_checks, "
                           "then report_done. Do not answer in prose.",
            })
            continue

        messages.append({
            "role": "assistant",
            "content": res.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in res.tool_calls
            ],
        })

        finished = False
        for tc in res.tool_calls:
            name = tc.function.name
            args = tools.parse_args(tc.function.arguments)
            result = await tools.dispatch(name, args, node, ctx, runner="worker")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result[:3000]})
            node.tool_steps += 1
            out.steps += 1
            ctx.trace.event("tool_call", node.id, tool=name, args=_safe_args(args),
                            result_head=result[:200], step=node.tool_steps)

            if name == "write_file":
                node.log(f"wrote {args.get('path', '?')}")
            if name == "report_done":
                out.status = str(args.get("status", "blocked"))
                out.summary = str(args.get("summary", ""))[:1200]
                out.needs_human = bool(args.get("needs_human"))
                out.human_question = args.get("human_question")
                out.claimed_done = out.status == "done"
                node.log(f"worker reports {out.status}"
                         + (" (needs human)" if out.needs_human else ""))
                finished = True
                break

            flag = await monitor.step(node, {"name": name, "args": args, "result": result}, ctx)
            if flag in ("LOOP", "DRIFT"):
                out.interrupted_by = flag
                node.log(f"worker interrupted by monitor: {flag}")
                finished = True
                break
            if flag == "BUDGET":
                messages.append({
                    "role": "user",
                    "content": "You have nearly spent your token allocation. Run the checks once "
                               "more, then call report_done and be honest about what passes.",
                })

        if finished:
            break
    else:
        out.interrupted_by = "STEPS"
        node.log(f"worker hit the {ctx.cfg.MAX_TOOL_STEPS_PER_LEAF}-step cap")

    # the worker's own last word on the checks, for the trace. The verifier re-runs them anyway.
    summary = await run_checks(node.dod, ctx, runner="worker")
    if not out.summary:
        out.summary = (f"worker ended without reporting; {summary['passed']}/{summary['total']} "
                       f"checks passing by its own run")
    ctx.trace.event("worker_done", node.id, status=out.status,
                    claimed_done=out.claimed_done, interrupted_by=out.interrupted_by,
                    self_checks=f"{summary['passed']}/{summary['total']}", steps=out.steps)
    return out


MAX_HISTORY_MESSAGES = 16


def _trim(messages: list[dict]) -> list[dict]:
    """Keep the system prompt, the original instruction, and the most recent exchanges.

    Without this, every worker call re-sends the whole transcript: the first live run spent
    23k tokens because nine steps of accumulated tool output rode along on each call.
    The cut never orphans a `tool` reply from its `assistant` tool_calls message.
    """
    if len(messages) <= MAX_HISTORY_MESSAGES:
        return messages
    head, tail = messages[:2], messages[2:]
    cut = max(0, len(tail) - (MAX_HISTORY_MESSAGES - 3))
    while cut < len(tail) and tail[cut].get("role") == "tool":
        cut += 1
    note = {"role": "user", "content": "[earlier steps elided to save budget — call run_checks "
                                       "if you need the current state of the checks.]"}
    return head + [note] + tail[cut:]


def _safe_args(args: dict) -> dict:
    """Keep file contents out of the event log; keep everything else."""
    out = {}
    for k, v in args.items():
        if k == "content":
            out[k] = f"<{len(str(v))} bytes>"
        else:
            out[k] = str(v)[:300]
    return out
