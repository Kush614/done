"""The invariants from SPEC §3.2 that make a 'done' claim mean something."""

from __future__ import annotations

import pytest

from done import dod_writer, planner, solver, verifier, worker
from done.config import Config, family
from done.models import Check, Node, Verification
from done.worker import WorkerOutcome


def _dod() -> list[Check]:
    return [Check(id="c1", description="it works", kind="shell", command="true", passed=True)]


@pytest.fixture
def stub(monkeypatch):
    """Patch the four LLM-backed stages so solve()'s control flow is what is under test."""
    calls = {"plan": 0, "dod_write": 0, "dod_rewrite": 0, "worker": 0, "verify": 0,
             "integration": 0, "summarize": 0, "replan": 0}

    async def fake_write(node, ctx):
        calls["dod_write"] += 1
        node_checks = _dod()
        return node_checks

    async def fake_rewrite(node, reasoning, ctx):
        calls["dod_rewrite"] += 1
        return _dod()

    async def fake_integration(node, children, ctx):
        calls["integration"] += 1
        return _dod()

    async def fake_worker(node, ctx, attempt=1, failing="", findings=""):
        calls["worker"] += 1
        return WorkerOutcome(status="done", summary="built it", claimed_done=True)

    async def fake_summarize(node, ctx):
        calls["summarize"] += 1
        return "summary"

    monkeypatch.setattr(dod_writer, "write", fake_write)
    monkeypatch.setattr(dod_writer, "rewrite", fake_rewrite)
    monkeypatch.setattr(dod_writer, "integration_checks", fake_integration)
    monkeypatch.setattr(worker, "execute", fake_worker)
    monkeypatch.setattr(solver, "summarize", fake_summarize)
    return calls


async def test_depth_cap_forces_execute(ctx, node_factory, stub, monkeypatch):
    async def must_not_plan(node, c):
        raise AssertionError("planner must not be consulted at the depth cap")

    monkeypatch.setattr(planner, "decide", must_not_plan)

    async def verify_done(node, c):
        v = Verification(verifier_model="google/gemma-4-31b-it", checks_passed=1,
                         checks_total=1, verdict="DONE", reasoning="ok")
        node.verification = v
        return v

    monkeypatch.setattr(verifier, "verify", verify_done)
    node = node_factory(id="root.1.1.1", depth=ctx.cfg.MAX_DEPTH, model="openai/gpt-oss-120b")
    out = await solver.solve(node, ctx)
    assert out.decision == "EXECUTE"
    assert out.status == "done"


async def test_low_budget_forces_execute(ctx, node_factory, stub, monkeypatch):
    async def must_not_plan(node, c):
        raise AssertionError("planner must not be consulted below MIN_SPLIT_BUDGET")

    monkeypatch.setattr(planner, "decide", must_not_plan)

    async def verify_done(node, c):
        v = Verification(verifier_model="google/gemma-4-31b-it", checks_passed=1,
                         checks_total=1, verdict="DONE", reasoning="ok")
        node.verification = v
        return v

    monkeypatch.setattr(verifier, "verify", verify_done)
    node = node_factory(depth=0, budget_tokens=ctx.cfg.MIN_SPLIT_BUDGET - 1,
                        model="openai/gpt-oss-120b")
    out = await solver.solve(node, ctx)
    assert out.decision == "EXECUTE"


async def test_split_not_done_when_a_child_fails(ctx, node_factory, stub, monkeypatch):
    """Invariant 2: a SPLIT node cannot be done unless every child is done."""
    async def plan_split(node, c):
        if node.id == "root":
            return "SPLIT", [
                {"goal": "build the storage module in storage.py", "why": "", "budget_share": 0.5},
                {"goal": "build the command line entry point cli.py", "why": "", "budget_share": 0.5},
            ]
        return "EXECUTE", []

    monkeypatch.setattr(planner, "decide", plan_split)

    async def verify(node, c):
        good = node.id != "root.2"
        v = Verification(verifier_model="google/gemma-4-31b-it",
                         checks_passed=1 if good else 0, checks_total=1,
                         verdict="DONE" if good else "NOT_DONE", reasoning="x")
        node.verification = v
        return v

    monkeypatch.setattr(verifier, "verify", verify)

    async def no_replan(node, reports, c):
        return {"needs_human": False, "human_question": None, "reasoning": "nothing left",
                "subgoals": []}

    monkeypatch.setattr(planner, "replan", no_replan)

    root = await solver.solve(node_factory(), ctx)
    assert root.status != "done"
    assert ctx.nodes["root.1"].status == "done"
    assert ctx.nodes["root.2"].status in ("stopped", "failed", "needs_human")
    assert stub["integration"] == 0, "integration checks must not run when a child failed"


async def test_verifier_must_be_a_different_family(ctx, node_factory, stub, monkeypatch):
    """Invariant 1: a same-family 'DONE' is rejected by solve(), not just discouraged."""
    async def verify_same_family(node, c):
        v = Verification(verifier_model="openai/gpt-oss-120b", checks_passed=1, checks_total=1,
                         verdict="DONE", reasoning="self-approved")
        node.verification = v
        return v

    monkeypatch.setattr(verifier, "verify", verify_same_family)
    monkeypatch.setattr(planner, "decide", lambda node, c: _execute())
    node = node_factory(depth=ctx.cfg.MAX_DEPTH, model="openai/gpt-oss-120b")
    out = await solver.solve(node, ctx)
    assert out.status == "failed"
    assert family(out.verification.verifier_model) == family(node.model)


async def test_config_never_maps_a_family_to_itself():
    cfg = Config()
    for fam, v in cfg.verifier_for_family.items():
        assert family(v) != fam, f"{fam} would verify itself via {v}"
    for depth in cfg.tiers:
        worker_model = cfg.tiers[depth]
        assert family(cfg.verifier_for(worker_model)) != family(worker_model)


async def test_checks_inadequate_rewrites_once_then_proceeds(ctx, node_factory, stub, monkeypatch):
    verdicts = iter(["CHECKS_INADEQUATE", "CHECKS_INADEQUATE", "DONE"])

    async def verify(node, c):
        verdict = next(verdicts, "DONE")
        v = Verification(verifier_model="google/gemma-4-31b-it", checks_passed=0, checks_total=1,
                         verdict=verdict, reasoning="your checks prove nothing")
        node.verification = v
        return v

    monkeypatch.setattr(verifier, "verify", verify)
    node = node_factory(depth=ctx.cfg.MAX_DEPTH, model="openai/gpt-oss-120b")
    out = await solver.solve(node, ctx)
    assert stub["dod_rewrite"] == 1, "checks are rewritten exactly once, never in a loop"
    assert out.status in ("done", "stopped")
    assert out.attempts <= ctx.cfg.MAX_ATTEMPTS


async def test_budget_split_reserves_for_the_parent(ctx, node_factory):
    node = node_factory(budget_tokens=100_000)
    children = solver.make_children(node, [
        {"goal": "a" * 20, "why": "", "budget_share": 0.5},
        {"goal": "b" * 20, "why": "", "budget_share": 0.5},
    ], ctx)
    assert sum(c.budget_tokens for c in children) <= node.budget_tokens
    assert sum(c.budget_tokens for c in children) <= node.budget_tokens * (
        1 - ctx.cfg.RESERVE_FRACTION) + 1
    assert all(c.depth == 1 for c in children)
    assert all(c.model == ctx.cfg.model_for_depth(1) for c in children)
    assert [c.id for c in children] == ["root.1", "root.2"]


async def test_children_inherit_assumptions_and_sibling_contracts(ctx, node_factory):
    node = node_factory()
    node.assumptions = ["notes are stored in ./notes.json"]
    children = solver.make_children(node, [
        {"goal": "build storage.py exposing load()/save()", "why": "shared by everyone",
         "budget_share": 0.5},
        {"goal": "build the cli on top of storage.py", "why": "", "budget_share": 0.5},
    ], ctx)
    ctxt = children[1].ancestors_context
    assert "notes.json" in ctxt
    assert "storage.py" in ctxt
    assert "root.1" in ctxt


def _execute():
    async def inner():
        return "EXECUTE", []
    return inner()
