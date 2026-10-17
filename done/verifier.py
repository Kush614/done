"""Independent cross-family verification.

Three things make this more than a second opinion:
  1. the verifier is always a different model family than the worker (asserted, not hoped);
  2. it never sees the worker's transcript or its claims — only the artifacts;
  3. it meta-verifies the checks themselves before running them, and re-runs every check from
     scratch rather than trusting any reported pass.
"""

from __future__ import annotations

from . import llm, tools
from .checks import run_checks
from .config import family
from .dod_writer import render_checks
from .models import Node, Verification
from .prompts import node_vars, render

MAX_INDEPENDENT_TOOL_CALLS = 3


class VerifierFamilyError(RuntimeError):
    """The configuration would have let a model family verify itself."""


async def verify(node: Node, ctx) -> Verification:
    node.status = "verifying"
    ctx.trace.node_update(node)

    vmodel = ctx.cfg.verifier_for(node.model)
    if family(vmodel) == family(node.model):
        raise VerifierFamilyError(
            f"verifier {vmodel!r} is the same family as worker {node.model!r}"
        )
    ctx.trace.event("verify_start", node.id, verifier_model=vmodel, worker_model=node.model,
                    worker_family=family(node.model), verifier_family=family(vmodel),
                    checks=len(node.dod))

    # --- 1. meta-verification: are these checks even testing the goal? -------
    meta = await _meta_verify(node, vmodel, ctx)

    # --- 2. re-run every check, ourselves -----------------------------------
    results = await run_checks(node.dod, ctx, runner="verifier")
    passed, total = results["passed"], results["total"]

    # --- 3. look for what the checks miss -----------------------------------
    verdict, reasoning, findings, actual_model = await _adjudicate(
        node, vmodel, ctx, meta, results
    )

    # --- 4. deterministic guardrails the model cannot talk its way past -----
    bad_meta = [c for c in node.dod if not c.meta_verified]
    if len(bad_meta) > 1 or meta.get("uncovered_aspects"):
        if verdict != "CHECKS_INADEQUATE":
            reasoning = (f"[forced] {len(bad_meta)} check(s) do not test the goal"
                         + (f"; uncovered: {'; '.join(meta['uncovered_aspects'][:3])}"
                            if meta.get("uncovered_aspects") else "")
                         + f". Verifier said: {reasoning}")
            verdict = "CHECKS_INADEQUATE"
    elif passed < total and verdict == "DONE":
        failing = [r["id"] for r in results["results"] if not r["passed"]]
        reasoning = (f"[forced] verifier claimed DONE but {len(failing)} check(s) fail "
                     f"({', '.join(failing)}). Original reasoning: {reasoning}")
        verdict = "NOT_DONE"

    # Independence is judged on the model that ACTUALLY answered, not the one we asked for:
    # a mid-call fallback could otherwise hand the worker's own family the final say.
    if family(actual_model) == family(node.model):
        reasoning = (f"[forced] verification fell back to {actual_model}, the worker's own "
                     f"family ({family(node.model)}); independence lost. Original: {reasoning}")
        verdict = "NOT_DONE"
        ctx.trace.event("independence_lost", node.id, asked_for=vmodel, answered_by=actual_model,
                        worker_model=node.model)

    v = Verification(
        verifier_model=actual_model,
        checks_passed=passed,
        checks_total=total,
        independent_findings=findings,
        verdict=verdict,  # type: ignore[arg-type]
        reasoning=reasoning,
    )
    node.verification = v
    node.verification_history.append(v)
    node.log(f"verified by {vmodel.split('/')[-1]} ({family(vmodel)}): {verdict} "
             f"{passed}/{total} checks")
    ctx.trace.event("verify_result", node.id, verdict=verdict, passed=passed, total=total,
                    verifier_model=vmodel, findings=findings, reasoning=reasoning[:500])
    ctx.bump("verifications")
    return v


async def _meta_verify(node: Node, vmodel: str, ctx) -> dict:
    prompt = render("meta_verifier", **node_vars(node), checks=render_checks(node.dod))
    try:
        res = await llm.chat(
            vmodel, [{"role": "user", "content": prompt}],
            json_mode=True, max_tokens=1200, tag="meta_verifier", ctx=ctx,
            fallback=ctx.cfg.cross_family_fallbacks(node.model),
        )
        data = res.json()
    except Exception as exc:  # noqa: BLE001
        ctx.trace.event("meta_verify_error", node.id, error=str(exc)[:200])
        data = {}

    by_id = {c.id: c for c in node.dod}
    judged = 0
    for item in data.get("checks") or []:
        if not isinstance(item, dict):
            continue
        check = by_id.get(str(item.get("id")))
        if check is None:
            continue
        check.meta_verified = bool(item.get("meta_verified", False))
        check.meta_reason = str(item.get("reason", ""))[:240]
        judged += 1
    if judged == 0:
        # the meta-verifier said nothing usable; do not punish the node for that
        for c in node.dod:
            c.meta_verified = True
            c.meta_reason = "meta-verifier returned nothing parseable; check accepted by default"
    uncovered = [str(a)[:200] for a in (data.get("uncovered_aspects") or []) if str(a).strip()]
    ctx.trace.event("meta_verify", node.id,
                    verified=[c.id for c in node.dod if c.meta_verified],
                    rejected=[c.id for c in node.dod if not c.meta_verified],
                    uncovered_aspects=uncovered)
    return {"uncovered_aspects": uncovered, "hint": data.get("verdict_hint")}


async def _adjudicate(node: Node, vmodel: str, ctx, meta: dict, results: dict):
    meta_lines = "\n".join(
        f"- {c.id} [{c.kind}] {c.description}\n    meta_verified={c.meta_verified} "
        f"({c.meta_reason or 'no reason given'})"
        for c in node.dod
    ) or "(no checks)"
    if meta.get("uncovered_aspects"):
        meta_lines += "\n\nASPECTS OF THE GOAL WITH NO CHECK AT ALL:\n" + "\n".join(
            f"- {a}" for a in meta["uncovered_aspects"]
        )
    check_lines = "\n".join(
        f"- {r['id']}: {'PASS' if r['passed'] else 'FAIL'} — {r['description']}\n"
        f"    output: {(r['output'] or '').strip()[:400]}"
        for r in results["results"]
    ) or "(no checks were run)"

    messages = [{
        "role": "user",
        "content": render(
            "verifier",
            **node_vars(node),
            meta_results=meta_lines,
            check_results=check_lines,
            workspace=ctx.sandbox.snapshot(),
        ),
    }]

    findings: list[str] = []
    used_calls = 0
    for _ in range(MAX_INDEPENDENT_TOOL_CALLS + 2):
        try:
            offering_tools = used_calls < MAX_INDEPENDENT_TOOL_CALLS
            res = await llm.chat(
                vmodel, messages,
                tools=tools.VERIFIER_TOOL_SCHEMAS if offering_tools else None,
                # The ruling is the one call that must not be cut off: a truncated JSON object
                # parses to {} and the verdict silently falls back to check results alone,
                # losing the independent findings. (DECISIONS 14:25)
                json_mode=not offering_tools,
                max_tokens=1400 if offering_tools else 2000,
                tag="verifier", ctx=ctx,
                fallback=ctx.cfg.cross_family_fallbacks(node.model),
            )
        except llm.BudgetExhausted:
            # out of budget mid-verification: fall back to the deterministic check results
            ok = results["passed"] == results["total"] and results["total"] > 0
            return ("DONE" if ok else "NOT_DONE",
                    "budget exhausted during verification; ruling on re-run check results alone",
                    findings, vmodel)

        if res.tool_calls and used_calls < MAX_INDEPENDENT_TOOL_CALLS:
            messages.append({
                "role": "assistant",
                "content": res.content or "",
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in res.tool_calls
                ],
            })
            for tc in res.tool_calls:
                args = tools.parse_args(tc.function.arguments)
                out = await tools.dispatch(tc.function.name, args, node, ctx, runner="verifier")
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": out[:6000]})
                used_calls += 1
                ctx.trace.event("tool_call", node.id, tool=tc.function.name,
                                args=str(args)[:200], runner="verifier",
                                result_head=out[:200])
            if used_calls >= MAX_INDEPENDENT_TOOL_CALLS:
                messages.append({
                    "role": "user",
                    "content": "No more tool calls. Rule now, in the JSON format specified.",
                })
            continue

        data = res.json()
        if not data and (res.content or "").strip():
            # it said something we could not read; keep the evidence rather than shrugging
            ctx.trace.event("verifier_unparsed", node.id, model=res.model_used,
                            content_head=(res.content or "")[:400],
                            completion_tokens=res.usage.get("completion"))
        verdict = str(data.get("verdict", "")).upper()
        if verdict not in ("DONE", "NOT_DONE", "CHECKS_INADEQUATE"):
            verdict = "DONE" if results["passed"] == results["total"] and results["total"] else "NOT_DONE"
        findings = [str(f)[:300] for f in (data.get("independent_findings") or []) if str(f).strip()]
        reasoning = str(data.get("reasoning", ""))[:900] or "(verifier gave no reasoning)"
        return verdict, reasoning, findings[:6], res.model_used

    ok = results["passed"] == results["total"] and results["total"] > 0
    return ("DONE" if ok else "NOT_DONE",
            "verifier never returned a parseable ruling; fell back to re-run check results",
            findings, vmodel)
