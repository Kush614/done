"""The definition-of-done writer. Runs BEFORE the worker, and again when a verifier
rejects its checks as inadequate — that second path is recursion applied to the checks."""

from __future__ import annotations

import json

from . import llm
from .models import Check, Node
from .prompts import node_vars, render

ALLOWED_KINDS = {"shell", "pytest", "file_exists", "json_schema", "llm_judge"}


async def write(node: Node, ctx) -> list[Check]:
    prompt = render("dod_writer", **node_vars(node), workspace=ctx.sandbox.snapshot())
    checks = await _ask(node, prompt, ctx, tag="dod_writer")
    node.log(f"definition of done: {len(checks)} checks")
    ctx.trace.event("dod", node.id, checks=[c.model_dump() for c in checks], phase="initial")
    return checks


async def rewrite(node: Node, verifier_reasoning: str, ctx) -> list[Check]:
    prompt = render(
        "dod_rewrite",
        **node_vars(node),
        old_checks=_render_checks(node.dod),
        verifier_reasoning=verifier_reasoning,
        workspace=ctx.sandbox.snapshot(),
    )
    checks = await _ask(node, prompt, ctx, tag="dod_rewrite")
    node.log(f"checks rejected as inadequate → rewrote {len(checks)} stricter checks")
    ctx.trace.event("dod", node.id, checks=[c.model_dump() for c in checks], phase="rewrite",
                    complaint=verifier_reasoning[:400])
    return checks


async def integration_checks(node: Node, children: list[Node], ctx) -> list[Check]:
    reports = "\n".join(
        f"- [{c.id}] {c.goal}\n  reports: {(c.final_report or 'no report').strip()}"
        for c in children
    )
    prompt = render(
        "integration_checks",
        **node_vars(node),
        child_reports=reports,
        workspace=ctx.sandbox.snapshot(),
    )
    checks = await _ask(node, prompt, ctx, tag="integration_checks")
    node.log(f"integration checks: {len(checks)} across {len(children)} children")
    ctx.trace.event("dod", node.id, checks=[c.model_dump() for c in checks], phase="integration")
    return checks


# A check failing because the work does not exist yet is correct and expected. A check failing
# because the check itself is broken is not — these are the signatures of the latter.
MALFORMED_SIGNS = (
    "syntaxerror", "invalid syntax", "command not found", "unexpected token",
    "unexpected eof", "bad substitution", "unrecognized arguments", "invalid option",
    "usage: python", "no such option",
)


async def falsify(node: Node, ctx) -> list[Check]:
    """Run the definition of done against the EMPTY workspace, before any work happens.

    Every non-negative check MUST fail here. One that passes is a tautology — it proves nothing
    about the goal. One that errors with a tooling error is malformed and can never pass, so the
    work would be failed for the check's bug (which is what happened on the first demo_01 run:
    `import notes.cli if hasattr(...) else None` is not valid Python).

    Deterministic and free: no model is consulted unless something is actually wrong.
    """
    from .checks import run_checks

    summary = await run_checks(node.dod, ctx, runner="baseline")
    tautologies = [c for c in node.dod if c.passed and not c.negative]
    malformed = [c for c in node.dod
                 if not c.passed and _looks_malformed(c.output or "")]

    ctx.trace.event("falsify", node.id, ran=summary["total"], passed_on_empty=summary["passed"],
                    tautologies=[c.id for c in tautologies],
                    malformed=[c.id for c in malformed])

    if tautologies or malformed:
        complaint_parts = []
        if tautologies:
            complaint_parts.append(
                "These checks PASS against an empty workspace, before any work was done, so they "
                "prove nothing about the goal — replace them with checks on actual behaviour and "
                "actual output values: "
                + "; ".join(f"{c.id} ({c.description}) — command was `{c.command or c.path}`"
                            for c in tautologies)
            )
        if malformed:
            complaint_parts.append(
                "These checks are BROKEN — they fail with a tooling/syntax error rather than an "
                "assertion failure, so they can never pass no matter what is built. Rewrite the "
                "command itself: "
                + "; ".join(f"{c.id} — command `{c.command}` produced: {(c.output or '')[:200]}"
                            for c in malformed)
            )
        node.log(f"falsification: {len(tautologies)} tautological, {len(malformed)} malformed "
                 f"check(s) — rewriting before any work starts")
        ctx.bump("checks_falsified")
        try:
            node.dod = await rewrite(node, "\n\n".join(complaint_parts), ctx)
        except Exception as exc:  # noqa: BLE001 - keep the original checks rather than none
            ctx.trace.event("falsify_rewrite_failed", node.id, error=str(exc)[:200])
        else:
            await run_checks(node.dod, ctx, runner="baseline2")
            still = [c.id for c in node.dod if c.passed and not c.negative]
            ctx.trace.event("falsify_after", node.id, still_tautological=still)

    # leave the checks unrun for the worker: a stale pass from the baseline would mislead it
    for c in node.dod:
        c.passed, c.output = None, None
    return node.dod


def _looks_malformed(output: str) -> bool:
    low = output.lower()
    # ImportError / "no such file" are the CORRECT pre-work failures, not broken checks
    return any(sign in low for sign in MALFORMED_SIGNS)


async def _ask(node: Node, prompt: str, ctx, *, tag: str) -> list[Check]:
    res = await llm.chat(
        node.model,
        [{"role": "user", "content": prompt}],
        json_mode=True, max_tokens=1800, tag=tag, ctx=ctx,
    )
    checks = _parse(res.json())
    if not checks:
        checks = _fallback_checks(node)
        node.log("check writer returned nothing usable — fell back to a minimal check set")
        ctx.trace.event("dod_fallback", node.id, tag=tag)
    return checks


def _parse(data: dict) -> list[Check]:
    raw = data.get("checks") or data.get("items") or []
    out: list[Check] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind", "shell")).lower()
        if kind not in ALLOWED_KINDS:
            kind = "shell"
        cid = str(item.get("id") or f"c{i + 1}")
        while cid in seen:
            cid += "x"
        seen.add(cid)
        command = item.get("command")
        if isinstance(command, (dict, list)):
            command = json.dumps(command)
        check = Check(
            id=cid,
            description=str(item.get("description", ""))[:300] or f"check {cid}",
            kind=kind,  # type: ignore[arg-type]
            command=str(command) if command else None,
            path=str(item["path"]) if item.get("path") else None,
            judge_prompt=str(item["judge_prompt"]) if item.get("judge_prompt") else None,
            negative=bool(item.get("negative")),
        )
        if check.kind in ("shell", "pytest") and not check.command:
            continue
        if check.kind in ("file_exists", "json_schema") and not check.path:
            continue
        if check.kind == "llm_judge" and not check.judge_prompt:
            check.judge_prompt = check.description
        out.append(check)
    # at most one llm_judge per node (SPEC §4.2)
    judges = [c for c in out if c.kind == "llm_judge"]
    for extra in judges[1:]:
        out.remove(extra)
    return out[:7]


def _fallback_checks(node: Node) -> list[Check]:
    """Never leave a node with zero checks — an unchecked node could claim done for free."""
    return [
        Check(id="f1", description="the workspace is not empty", kind="shell",
              command="test \"$(ls -A . | wc -l)\" -gt 0"),
        Check(id="f2", description="every python file in the workspace compiles", kind="shell",
              command="python3 -c \"import pathlib,py_compile,sys; "
                      "fs=[p for p in pathlib.Path('.').rglob('*.py')]; "
                      "[py_compile.compile(str(p), doraise=True) for p in fs]; "
                      "sys.exit(0 if fs else 1)\""),
        Check(id="f3", description="no placeholder TODO/FIXME left in the deliverable",
              kind="shell", negative=True,
              command="grep -rIl -E 'TODO|FIXME|NotImplementedError' --include='*.py' . "),
    ]


def _render_checks(checks: list[Check]) -> str:
    return "\n".join(
        f"- {c.id} [{c.kind}{' NEGATIVE' if c.negative else ''}] {c.description}"
        + (f"\n    command: {c.command}" if c.command else "")
        + (f"\n    path: {c.path}" if c.path else "")
        for c in checks
    ) or "(none)"


render_checks = _render_checks
