"""The check runner. Shared by the worker (which must satisfy checks) and the verifier
(which never trusts the worker's pass/fail and re-runs everything itself)."""

from __future__ import annotations

import json
import sys
from typing import Any

from .models import Check
from .prompts import render
from .sandbox import SandboxViolation

PYTHON = sys.executable or "python3"


async def run_checks(node_checks: list[Check], ctx: Any, *, check_ids: list[str] | None = None,
                     runner: str = "worker") -> dict:
    """Run each check per its kind, fill passed/output in place, return a summary dict."""
    selected = [c for c in node_checks if not check_ids or c.id in check_ids]
    for check in selected:
        try:
            check.passed, check.output = await _run_one(check, ctx)
        except SandboxViolation as exc:
            check.passed, check.output = False, f"[sandbox refused] {exc}"
        except Exception as exc:  # noqa: BLE001 - a broken check is a failed check, not a crash
            check.passed, check.output = False, f"[check error] {type(exc).__name__}: {exc}"
    passed = sum(1 for c in selected if c.passed)
    summary = {
        "runner": runner,
        "passed": passed,
        "total": len(selected),
        "results": [
            {"id": c.id, "passed": c.passed, "description": c.description,
             "kind": c.kind, "output": (c.output or "")[:600]}
            for c in selected
        ],
    }
    if ctx is not None and getattr(ctx, "trace", None):
        ctx.trace.event("run_checks", getattr(ctx, "_current_node_id", None),
                        runner=runner, passed=passed, total=len(selected),
                        failing=[c.id for c in selected if not c.passed])
    return summary


async def _run_one(check: Check, ctx: Any) -> tuple[bool, str]:
    sb = ctx.sandbox
    if check.kind == "file_exists":
        path = sb.resolve(check.path or "")
        exists = path.exists()
        ok = (not exists) if check.negative else exists
        return ok, f"{'exists' if exists else 'missing'}: {check.path}"

    if check.kind in ("shell", "pytest"):
        cmd = (check.command or "").strip()
        if not cmd:
            return False, "[no command on check]"
        if check.kind == "pytest" and "pytest" not in cmd:
            cmd = f"{PYTHON} -m pytest -q {cmd}"
        cmd = cmd.replace("python3", PYTHON).replace("python ", f"{PYTHON} ")
        res = await sb.run_shell(cmd)
        ok = res.ok
        if check.negative:
            # a negative check passes when the command FAILS (the bad thing didn't work)
            ok = not res.ok
        return ok, res.summary()

    if check.kind == "json_schema":
        path = sb.resolve(check.path or "")
        if not path.exists():
            return False, f"missing: {check.path}"
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            return False, f"invalid JSON: {exc}"
        spec = {}
        if check.command:
            try:
                spec = json.loads(check.command)
            except json.JSONDecodeError:
                spec = {}
        wanted_type = spec.get("type")
        if wanted_type == "array" and not isinstance(data, list):
            return False, f"expected array, got {type(data).__name__}"
        if wanted_type == "object" and not isinstance(data, dict):
            return False, f"expected object, got {type(data).__name__}"
        required = spec.get("required") or []
        probe = data[0] if isinstance(data, list) and data else data
        if isinstance(probe, dict):
            missing = [k for k in required if k not in probe]
            if missing:
                return False, f"missing keys {missing} in {check.path}"
        return True, f"valid JSON ({type(data).__name__}) at {check.path}"

    if check.kind == "llm_judge":
        return await _llm_judge(check, ctx)

    return False, f"[unknown check kind: {check.kind}]"


async def _llm_judge(check: Check, ctx: Any) -> tuple[bool, str]:
    """Last-resort subjective check. Judged by a cheap model against the rubric in the check."""
    from . import llm

    prompt = render(
        "llm_judge",
        rubric=check.judge_prompt or check.description,
        description=check.description,
        workspace=ctx.sandbox.snapshot(),
        files=_relevant_files(check, ctx),
    )
    res = await llm.chat(
        ctx.cfg.drift_judge_model,
        [{"role": "user", "content": prompt}],
        json_mode=True, max_tokens=400, tag="llm_judge", ctx=ctx,
    )
    data = res.json()
    ok = bool(data.get("passed"))
    return ok, f"[llm_judge {res.model_used}] {data.get('reason', '')[:400]}"


def _relevant_files(check: Check, ctx: Any) -> str:
    """Give the judge the file it is judging, if the check names one."""
    if check.path:
        return f"--- {check.path} ---\n{ctx.sandbox.read_file(check.path, limit=6000)}"
    chunks = []
    for name in ("README.md", "readme.md"):
        body = ctx.sandbox.read_file(name, limit=6000)
        if not body.startswith("[no such file"):
            chunks.append(f"--- {name} ---\n{body}")
            break
    return "\n\n".join(chunks) or "[no file named by the check]"
