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
