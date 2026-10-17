"""EXECUTE or SPLIT, and if SPLIT, what the children get. Also owns re-planning."""

from __future__ import annotations

import re

from . import llm
from .models import Node
from .prompts import node_vars, render


async def decide(node: Node, ctx) -> tuple[str, list[dict]]:
    """Ask the node's own model whether to do the work or split it."""
    node.status = "planning"
    ctx.trace.node_update(node)
    # The root owns the decomposition and the layout contract, so it gets the full prompt with
    # the worked example. A child is almost always answering "EXECUTE, my parent already
    # decomposed this" — at 3.2k tokens a call that was 13k of pure overhead on a four-way
    # split. (DECISIONS 15:35)
    prompt = render(
        "planner" if node.depth == 0 else "planner_leaf",
        **node_vars(node),
        max_depth=ctx.cfg.MAX_DEPTH,
        max_children=ctx.cfg.MAX_CHILDREN,
    )
    res = await llm.chat(
        node.model,
        [{"role": "user", "content": prompt}],
        json_mode=True, max_tokens=1200, tag="planner", ctx=ctx,
    )
    data = res.json()
    decision = str(data.get("decision", "EXECUTE")).upper()
    if decision not in ("EXECUTE", "SPLIT"):
        decision = "EXECUTE"

    assumptions = [str(a).strip() for a in (data.get("assumptions") or []) if str(a).strip()]
    node.assumptions = assumptions[:8]
    # The prompt has always asked for a "NEEDS HUMAN: " prefix on an assumption only a person
    # can settle — but nothing consumed it, so a goal needing SMTP credentials quietly became
    # "# Configuration - user must set these" and reported success. (DECISIONS 15:40)
    node.human_questions = [a.split(":", 1)[-1].strip() for a in assumptions
                            if a.upper().replace("-", " ").startswith("NEEDS HUMAN")]
    if node.human_questions:
        node.log(f"{len(node.human_questions)} assumption(s) need a human")
        ctx.trace.event("human_assumption", node.id, questions=node.human_questions)
    reasoning = str(data.get("reasoning", ""))[:600]

    subgoals = _clean_subgoals(data.get("subgoals"), node, ctx)

    # The root's job is decomposition. It was answering EXECUTE for a four-artifact goal about
    # half the time, which put leaf work on the most expensive model in the config and made the
    # run cost MORE than the all-root-model baseline. Ask once more, explicitly. (DECISIONS 15:55)
    if (decision == "EXECUTE" and node.depth == 0 and not subgoals
            and node.budget_tokens >= ctx.cfg.MIN_SPLIT_BUDGET * 4
            and not _names_one_file(node.goal)):
        ctx.trace.event("root_execute_challenged", node.id, reasoning=reasoning)
        node.log("root chose EXECUTE for a multi-artifact goal — re-asking for a decomposition")
        res2 = await llm.chat(
            node.model,
            [{"role": "user", "content": prompt},
             {"role": "assistant", "content": res.content or ""},
             {"role": "user", "content":
                 "You are the ROOT of the tree and your budget is large. Decomposing is your "
                 "job: the cheaper models below you exist to do the work, and each piece gets "
                 "verified in isolation only if it is its own sub-goal. Unless this goal is "
                 "literally a single file, split it. List the artifacts it contains, then "
                 "return the same JSON object with decision SPLIT and one sub-goal per "
                 "artifact, each naming its own files and honouring the layout in your "
                 "assumptions."}],
            json_mode=True, max_tokens=1400, tag="planner", ctx=ctx,
        )
        data2 = res2.json()
        subgoals2 = _clean_subgoals(data2.get("subgoals"), node, ctx)
        if len(subgoals2) >= 2:
            decision, subgoals = "SPLIT", subgoals2
            reasoning = str(data2.get("reasoning", reasoning))[:600]
            extra = [str(a).strip() for a in (data2.get("assumptions") or []) if str(a).strip()]
            if extra:
                node.assumptions = extra[:8]
        else:
            node.log("root stood by EXECUTE on a second ask; accepting it")

    if decision == "SPLIT" and len(subgoals) < 2:
        decision = "EXECUTE"  # a "split" into one child is just a loop wearing a hat
        node.log("planner proposed <2 sub-goals; treating as EXECUTE")
        subgoals = []

    node.decision = decision
    node.log(f"planner → {decision}" + (f" into {len(subgoals)} sub-goals" if subgoals else ""))
    ctx.trace.event("plan", node.id, decision=decision, reasoning=reasoning,
                    assumptions=node.assumptions,
                    subgoals=[s["goal"] for s in subgoals], model=res.model_used)
    if assumptions:
        node.log(f"{len(assumptions)} assumption(s) declared")
    return decision, subgoals


async def replan(node: Node, child_reports: str, ctx) -> dict:
    """A decomposition partly failed. Ask for a smaller one that routes around the failure."""
    node.status = "replanning"
    ctx.trace.node_update(node)
    prompt = render(
        "replan",
        **node_vars(node),
        child_reports=child_reports,
        workspace=ctx.sandbox.snapshot(),
    )
    res = await llm.chat(
        node.model,
        [{"role": "user", "content": prompt}],
        json_mode=True, max_tokens=1000, tag="replan", ctx=ctx,
    )
    data = res.json()
    out = {
        "needs_human": bool(data.get("needs_human")),
        "human_question": data.get("human_question"),
        "reasoning": str(data.get("reasoning", ""))[:600],
        "subgoals": _clean_subgoals(data.get("subgoals"), node, ctx, limit=3),
    }
    ctx.trace.event("replan", node.id, **{k: v for k, v in out.items() if k != "subgoals"},
                    subgoals=[s["goal"] for s in out["subgoals"]], model=res.model_used)
    return out


def _clean_subgoals(raw, node: Node, ctx, limit: int | None = None) -> list[dict]:
    """Drop junk, drop restatements of the parent goal, normalize budget shares."""
    limit = limit or ctx.cfg.MAX_CHILDREN
    out: list[dict] = []
    for item in raw or []:
        if isinstance(item, str):
            item = {"goal": item}
        if not isinstance(item, dict):
            continue
        goal = str(item.get("goal", "")).strip()
        if len(goal) < 8:
            continue
        if _is_restatement(goal, node.goal):
            node.log(f"dropped sub-goal that restates the parent: {goal[:60]!r}")
            ctx.trace.event("subgoal_rejected", node.id, goal=goal, reason="restates parent goal")
            continue
        try:
            share = float(item.get("budget_share", 0) or 0)
        except (TypeError, ValueError):
            share = 0.0
        out.append({"goal": goal, "why": str(item.get("why", ""))[:400], "budget_share": share,
                    "prerequisite": bool(item.get("prerequisite"))})
        if len(out) >= limit:
            break
    # at most one prerequisite, and only if it is not the only child
    prereqs = [s for s in out if s["prerequisite"]]
    for extra in prereqs[1:]:
        extra["prerequisite"] = False
    if len(out) < 2:
        for s in out:
            s["prerequisite"] = False

    total = sum(s["budget_share"] for s in out)
    if out:
        if total <= 0:
            for s in out:
                s["budget_share"] = 1.0 / len(out)
        else:
            for s in out:
                s["budget_share"] /= total
    return out


_ONE_FILE = re.compile(r"[\w./-]+\.(?:py|md|txt|json|toml|cfg|ini|sh|yaml|yml|html|css|js)\b")


def _names_one_file(goal: str) -> bool:
    named = {m.group(0).lstrip("./") for m in _ONE_FILE.finditer(goal)}
    return len(named) == 1


def _is_restatement(subgoal: str, parent_goal: str) -> bool:
    a, b = _norm(subgoal), _norm(parent_goal)
    if a == b:
        return True
    wa, wb = set(a.split()), set(b.split())
    if not wa or not wb:
        return False
    overlap = len(wa & wb) / max(len(wa), len(wb))
    return overlap > 0.85


def _norm(s: str) -> str:
    kept = "".join(c if (c.isalnum() or c.isspace()) else " " for c in s.lower())
    return " ".join(kept.split())
