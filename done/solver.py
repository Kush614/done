"""solve() — the recursive core.

A node either does the work itself or splits and calls solve() on each child with a smaller
budget and a cheaper model. Nothing in this file lets a node reach status "done" without a
different model family signing off (that gate lives in verify_and_decide).
"""

from __future__ import annotations

import asyncio
import re

from . import dod_writer, llm, monitor, planner, verifier, worker
from .config import family
from .models import Node
from .prompts import node_vars, render


_ARTIFACT = re.compile(r"[\w./-]+\.(?:py|md|txt|json|toml|cfg|ini|sh|yaml|yml|html|css|js)\b")


def _single_artifact(goal: str) -> bool:
    """True when the goal names exactly one file. One file is one artifact: do it, do not split it.

    Only fires when the goal is explicit about paths, which is exactly the case where a parent
    has already done the decomposition.
    """
    named = {m.group(0).lstrip("./") for m in _ARTIFACT.finditer(goal)}
    return len(named) == 1


async def solve(node: Node, ctx) -> Node:
    ctx.enter(node)
    ctx.trace.node_update(node)

    # --- base cases: three independent reasons a node may not split ---------
    if node.depth >= ctx.cfg.MAX_DEPTH:
        node.decision = "EXECUTE"
        node.log(f"at max depth {ctx.cfg.MAX_DEPTH} → must EXECUTE")
        subgoals: list[dict] = []
    elif node.budget_tokens < ctx.cfg.MIN_SPLIT_BUDGET:
        node.decision = "EXECUTE"
        node.log(f"budget {node.budget_tokens} < {ctx.cfg.MIN_SPLIT_BUDGET} → must EXECUTE")
        subgoals = []
    elif _single_artifact(node.goal):
        # Deterministic, because the planner cannot be trusted with it: a sub-goal that names
        # exactly one file is one artifact, and splitting it produced four Nemotron leaves
        # arguing over one module while their siblings got nothing. (DECISIONS 15:20)
        node.decision = "EXECUTE"
        node.log("goal names a single artifact → EXECUTE (no planner call)")
        ctx.trace.event("single_artifact", node.id, goal=node.goal)
        subgoals = []
    else:
        try:
            node.decision, subgoals = await planner.decide(node, ctx)
        except llm.BudgetExhausted as exc:
            return _stop(node, ctx, f"out of budget before planning: {exc}")

    if node.decision == "SPLIT":
        return await _split(node, subgoals, ctx)
    return await _execute(node, ctx)


# --------------------------------------------------------------------------- SPLIT
async def _split(node: Node, subgoals: list[dict], ctx) -> Node:
    children = make_children(node, subgoals, ctx)
    node.children = [c.id for c in children]
    ctx.trace.node_update(node)
    ctx.trace.event("split", node.id,
                    children=[{"id": c.id, "goal": c.goal, "budget": c.budget_tokens,
                               "model": c.model} for c in children])

    # A declared prerequisite runs alone, first. Siblings then start from the workspace as it
    # ACTUALLY is, not from a description of what the prerequisite was supposed to produce.
    # Without this, one sibling wrote storage.py flat while another built a notes/ package and
    # a third's tests could find neither. (DECISIONS 15:10)
    prereq = next((c for c in children if c.is_prerequisite), None)
    results: list = []
    if prereq is not None and len(children) > 1:
        ctx.trace.event("prerequisite_first", node.id, child=prereq.id, goal=prereq.goal)
        try:
            first = await solve(prereq, ctx)
        except BaseException as exc:  # noqa: BLE001 - recorded per-child below
            first = exc
        results.append(first)
        rest = [c for c in children if c is not prereq]
        if isinstance(first, Node):
            shared = _prerequisite_context(node, first, ctx)
            for c in rest:
                c.ancestors_context = shared
        ctx.enter(node)
        results += list(await asyncio.gather(*(solve(c, ctx) for c in rest),
                                             return_exceptions=True))
        children = [prereq] + rest
    else:
        results = list(await asyncio.gather(*(solve(c, ctx) for c in children),
                                            return_exceptions=True))
    settled: list[Node] = []
    for child, result in zip(children, results):
        if isinstance(result, BaseException):
            child.status = "failed"
            child.log(f"crashed: {type(result).__name__}: {result}")
            ctx.trace.event("node_error", child.id, error=str(result)[:400])
            ctx.trace.node_update(child)
            settled.append(child)
        else:
            settled.append(result)

    # Children's spend is charged to the children themselves (llm.charge). Subtree totals are
    # recomputed on every tree write, so nothing here needs to roll anything up.

    if any(c.status == "needs_human" for c in settled):
        blockers = [c for c in settled if c.status == "needs_human"]
        node.status = "needs_human"
        node.final_report = (
            f"{len(settled) - len(blockers)}/{len(settled)} sub-goals verified done, but this "
            f"cannot be declared done: " + " | ".join(
                (c.final_report or c.goal) for c in blockers
            )
        )
        node.log("a sub-goal needs a human; refusing to report done")
        ctx.trace.node_update(node)
        return node

    if all(c.status == "done" for c in settled):
        ctx.enter(node)
        try:
            node.dod = await dod_writer.integration_checks(node, settled, ctx)
        except llm.BudgetExhausted as exc:
            return _stop(node, ctx, f"children done but no budget left to verify integration: {exc}")
        node.status = "verifying"
        ctx.trace.node_update(node)
        return await verify_and_decide(node, ctx)

    return await replan_or_stop(node, settled, ctx)


def make_children(node: Node, subgoals: list[dict], ctx) -> list[Node]:
    """Budget split per SPEC §5.2: parent keeps a reserve, children divide the rest by share."""
    reserve = int(node.budget_tokens * ctx.cfg.RESERVE_FRACTION)
    available = max(0, node.budget_tokens - reserve)
    children: list[Node] = []
    context = _child_context(node, subgoals)
    for i, sg in enumerate(subgoals, start=1):
        alloc = max(ctx.cfg.MIN_LEAF_BUDGET, int(available * sg["budget_share"]))
        depth = node.depth + 1
        children.append(Node(
            id=f"{node.id}.{i}",
            parent_id=node.id,
            depth=depth,
            goal=sg["goal"],
            root_goal=node.root_goal,
            ancestors_context=context,
            budget_tokens=alloc,
            model=ctx.cfg.model_for_depth(depth),
            is_prerequisite=bool(sg.get("prerequisite")),
        ))
    return children


def _prerequisite_context(parent: Node, prereq: Node, ctx) -> str:
    """What the prerequisite actually built, as the siblings' starting truth."""
    lines = [prereq.ancestors_context.strip()] if prereq.ancestors_context else []
    lines.append(f"- [{prereq.id}] HAS ALREADY FINISHED ({prereq.status}): {prereq.goal}")
    if prereq.final_report:
        lines.append(f"  it reports: {prereq.final_report.strip()}")
    lines.append("  Its files are already in the workspace. The workspace right now contains:")
    for entry in ctx.sandbox.snapshot().splitlines()[:40]:
        lines.append(f"    {entry}")
    lines.append("  USE these files and these exact paths. Do not create a parallel layout, do "
                 "not rename them, and do not re-implement what they already provide — read them "
                 "first with read_file if you need the interface.")
    return "\n".join(lines)


def _child_context(node: Node, subgoals: list[dict]) -> str:
    """Shared-brain-lite: what a child must know that only its ancestors can tell it."""
    lines: list[str] = []
    if node.ancestors_context:
        lines.append(node.ancestors_context.strip())
    lines.append(f"- [{node.id}] was asked to: {node.goal}")
    for a in node.assumptions:
        lines.append(f"- assumption from [{node.id}]: {a}")
    if subgoals:
        lines.append(f"- [{node.id}] split the work; your siblings are handling:")
        for i, sg in enumerate(subgoals, start=1):
            lines.append(f"    - [{node.id}.{i}] {sg['goal']}"
                         + (f" — {sg['why']}" if sg.get("why") else ""))
        lines.append("  Do not build a sibling's piece. Use what they expose, as described above.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- EXECUTE
async def _execute(node: Node, ctx) -> Node:
    ctx.enter(node)
    try:
        node.dod = await dod_writer.write(node, ctx)       # the checks exist BEFORE the work does
        # ...and are falsified against the empty workspace before the work begins: a check that
        # already passes is a tautology, one that errors is broken.
        node.dod = await dod_writer.falsify(node, ctx)
    except llm.BudgetExhausted as exc:
        return _stop(node, ctx, f"out of budget before writing a definition of done: {exc}")
    ctx.trace.node_update(node)

    try:
        outcome = await worker.execute(node, ctx, attempt=node.attempts + 1)
    except llm.BudgetExhausted as exc:
        return _stop(node, ctx, f"out of budget during work: {exc}")

    if outcome.needs_human:
        node.status = "needs_human"
        node.final_report = (
            f"BLOCKED, needs a human: {outcome.human_question or outcome.summary}"
        )
        node.log("worker asked for a human")
        ctx.trace.event("needs_human", node.id, question=outcome.human_question,
                        summary=outcome.summary)
        ctx.trace.node_update(node)
        return node

    node.status = "verifying"
    ctx.trace.node_update(node)
    return await verify_and_decide(node, ctx)


# --------------------------------------------------------------------------- the gate
async def verify_and_decide(node: Node, ctx) -> Node:
    ctx.enter(node)
    try:
        v = await verifier.verify(node, ctx)
    except llm.BudgetExhausted as exc:
        return _stop(node, ctx, f"out of budget before verification could finish: {exc}")
    ctx.trace.node_update(node)

    if v.verdict == "DONE":
        # invariant 1: a different family signed off, or this is not done.
        if family(v.verifier_model) == family(node.model):
            node.log("REJECTED a same-family verification")
            node.status = "failed"
            ctx.trace.node_update(node)
            return node
        node.status = "done"
        node.final_report = await summarize(node, ctx)
        ctx.trace.node_update(node)
        return node

    if v.verdict == "CHECKS_INADEQUATE":
        if node.dod_rewrites < 1:
            # The complaint is about the CHECKS, not the work. Rewrite them and re-verify
            # against the artifacts that already exist — re-running the worker here would be
            # answering a question nobody asked, and it is what exhausted the first budgets.
            node.dod_rewrites += 1
            ctx.bump("checks_rewritten")
            try:
                node.dod = await dod_writer.rewrite(node, v.reasoning, ctx)
            except llm.BudgetExhausted as exc:
                return _stop(node, ctx, f"out of budget while rewriting checks: {exc}")
            node.log("re-verifying against the stricter checks without redoing the work")
            ctx.trace.node_update(node)
            return await verify_and_decide(node, ctx)

        # Already strengthened once. If the stricter set passes, that is as much assurance as
        # this node can buy — accept it, and carry the verifier's remaining doubts into the
        # report rather than burning the budget to argue.
        if v.checks_total and v.checks_passed == v.checks_total:
            v.verdict = "DONE"
            v.reasoning = ("checks were strengthened once and the stricter set passes; the "
                           "verifier still considers coverage imperfect — see findings. "
                           + v.reasoning)
            node.status = "done"
            node.log("accepted after one round of strengthened checks (caveats recorded)")
            ctx.trace.event("accepted_with_caveats", node.id,
                            findings=v.independent_findings, verifier=v.verifier_model)
            node.final_report = await summarize(node, ctx)
            ctx.trace.node_update(node)
            return node

    node.attempts += 1
    return await replan_or_stop(node, [], ctx)


# --------------------------------------------------------------------------- re-plan or stop
async def replan_or_stop(node: Node, children: list[Node], ctx) -> Node:
    ctx.enter(node)
    st = monitor.state_for(node, ctx)
    drift_flags = sum(1 for f in node.flags if f.kind == "DRIFT")

    # the honest answer takes precedence over trying again: if the goal provably needs a human,
    # a second attempt is just a more expensive way to fail. (DECISIONS.md, 13:40)
    if st.impossible or drift_flags >= 2:
        node.status = "needs_human"
        question = st.impossible_reason or "The goal appears unreachable from this sandbox."
        node.final_report = (
            f"BLOCKED, needs a human: {question}\n"
            f"Question for you: {_human_question(node, question)}"
        )
        node.log("escalating to a human")
        ctx.trace.event("needs_human", node.id, question=question,
                        drift_flags=drift_flags, impossible=st.impossible)
        ctx.trace.node_update(node)
        return node

    if node.attempts >= ctx.cfg.MAX_ATTEMPTS or ctx.budget.remaining < ctx.cfg.MIN_SPLIT_BUDGET:
        why = ("attempt cap reached" if node.attempts >= ctx.cfg.MAX_ATTEMPTS
               else f"run budget nearly gone ({ctx.budget.remaining} tokens left)")
        return _stop(node, ctx, why)

    v = node.verification
    if node.decision == "EXECUTE" and v is not None and v.verdict in ("NOT_DONE", "CHECKS_INADEQUATE"):
        failing = ", ".join(c.id for c in node.dod if c.passed is False) or "(none named)"
        node.log(f"re-running the work, attempt {node.attempts + 1}, on: {failing}")
        ctx.trace.event("retry", node.id, attempt=node.attempts + 1, failing=failing)
        try:
            outcome = await worker.execute(
                node, ctx, attempt=node.attempts + 1,
                failing=_failing_block(node),
                findings="\n".join(f"- {f}" for f in v.independent_findings) or v.reasoning,
            )
        except llm.BudgetExhausted as exc:
            return _stop(node, ctx, f"out of budget on the retry: {exc}")
        if outcome.needs_human:
            node.status = "needs_human"
            node.final_report = f"BLOCKED, needs a human: {outcome.human_question or outcome.summary}"
            ctx.trace.node_update(node)
            return node
        return await verify_and_decide(node, ctx)

    if node.decision == "SPLIT" and children:
        reports = "\n".join(
            f"- [{c.id}] {c.status.upper()}: {c.goal}\n  {(c.final_report or 'no report').strip()}"
            for c in children
        )
        try:
            plan = await planner.replan(node, reports, ctx)
        except llm.BudgetExhausted as exc:
            return _stop(node, ctx, f"out of budget before re-planning: {exc}")
        if plan["needs_human"] or not plan["subgoals"]:
            node.status = "needs_human" if plan["needs_human"] else "stopped"
            node.final_report = (
                f"BLOCKED, needs a human: {plan['human_question']}" if plan["needs_human"]
                else f"stopped: {plan['reasoning'] or 'no viable decomposition remained'}\n"
                     + _child_summary(children)
            )
            ctx.trace.node_update(node)
            return node
        node.log(f"re-planned around {sum(1 for c in children if c.status != 'done')} failed "
                 f"sub-goal(s) into {len(plan['subgoals'])} new sub-goal(s)")
        extra = make_children(node, plan["subgoals"], ctx)
        for i, child in enumerate(extra, start=len(node.children) + 1):
            child.id = f"{node.id}.{i}"
        node.children += [c.id for c in extra]
        ctx.trace.node_update(node)
        retried = await asyncio.gather(*(solve(c, ctx) for c in extra), return_exceptions=True)
        settled = [r for r in retried if isinstance(r, Node)]
        if settled and all(c.status == "done" for c in settled):
            survivors = [c for c in children if c.status == "done"] + settled
            try:
                node.dod = await dod_writer.integration_checks(node, survivors, ctx)
            except llm.BudgetExhausted as exc:
                return _stop(node, ctx, f"no budget left to verify integration: {exc}")
            return await verify_and_decide(node, ctx)
        return _stop(node, ctx, "the re-planned sub-goals did not all verify either",
                     children=children + settled)

    return _stop(node, ctx, "no remaining strategy", children=children)


def _stop(node: Node, ctx, why: str, children: list[Node] | None = None) -> Node:
    """An honest stop. Says what passed, what failed, and why we are not claiming done."""
    node.status = "stopped"
    v = node.verification
    passed = f"{v.checks_passed}/{v.checks_total}" if v else "0/0"
    failing = [c.id for c in node.dod if c.passed is False]
    parts = [f"STOPPED: {why}.", f"Checks passing: {passed}."]
    if failing:
        parts.append(f"Still failing: {', '.join(failing)}.")
    if v and v.independent_findings:
        parts.append("Verifier found: " + "; ".join(v.independent_findings[:3]) + ".")
    if children:
        parts.append(_child_summary(children))
    node.final_report = " ".join(parts)
    node.log(f"stopped: {why}")
    ctx.trace.event("stopped", node.id, why=why, checks=passed, failing=failing)
    ctx.trace.node_update(node)
    return node


def _child_summary(children: list[Node]) -> str:
    return "Sub-goals: " + "; ".join(f"[{c.id}] {c.status}" for c in children) + "."


def _failing_block(node: Node) -> str:
    return "\n".join(
        f"- {c.id} FAILED: {c.description}\n    output: {(c.output or '').strip()[:400]}"
        for c in node.dod if c.passed is False
    ) or "(the verifier re-ran the checks and was not satisfied)"


def _human_question(node: Node, reason: str) -> str:
    return (f"To finish \"{node.goal[:90]}\" I need something this sandbox cannot provide "
            f"({reason[:120]}). What should I use?")


async def summarize(node: Node, ctx) -> str:
    v = node.verification
    prompt = render(
        "summarize",
        **node_vars(node),
        verifier_model=v.verifier_model if v else "?",
        verdict=v.verdict if v else "?",
        checks_passed=v.checks_passed if v else 0,
        checks_total=v.checks_total if v else 0,
        findings="; ".join(v.independent_findings) if v and v.independent_findings else "(none)",
        workspace=ctx.sandbox.snapshot(),
    )
    try:
        res = await llm.chat(
            node.model, [{"role": "user", "content": prompt}],
            max_tokens=320, tag="summarize", ctx=ctx,
        )
        text = (res.content or "").strip()
    except Exception:  # noqa: BLE001 - a missing summary must not undo a verified node
        text = ""
    if not text:
        text = (f"Verified done: {node.goal} "
                f"({v.checks_passed}/{v.checks_total} checks re-run by "
                f"{v.verifier_model if v else 'a verifier'})")
    return text[:1200]
