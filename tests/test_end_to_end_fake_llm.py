"""End-to-end through the REAL worker loop, tools, sandbox, check runner and verifier gate —
with only the model calls faked. This is the wiring test: everything but the network.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from done import llm, solver
from done.config import family
from done.llm import LLMResult
from done.models import Node

STORAGE = '''import json, pathlib
PATH = pathlib.Path("notes.json")
def load():
    return json.loads(PATH.read_text()) if PATH.exists() else []
def save(notes):
    PATH.write_text(json.dumps(notes, indent=2))
def add(text):
    notes = load()
    note = {"id": (max([n["id"] for n in notes], default=0) + 1), "text": text}
    notes.append(note); save(notes); return note
def search(term):
    return [n for n in load() if term.lower() in n["text"].lower()]
'''

TEST_FILE = '''import storage
def test_add_then_search_is_case_insensitive(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    storage.PATH = tmp_path / "notes.json"
    storage.add("Buy MILK")
    assert len(storage.search("milk")) == 1
'''


def _tool_call(cid: str, name: str, args: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id=cid, type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(args)),
    )


def _result(content=None, tool_calls=None, model="openai/gpt-oss-120b") -> LLMResult:
    return LLMResult(content=content, tool_calls=tool_calls or [],
                     usage={"prompt": 400, "completion": 120, "total": 520},
                     model_used=model, latency_ms=10)


class FakeModels:
    """A scripted, cooperative model farm. Writes real files; the checks really run."""

    def __init__(self, *, split: bool, lie: bool = False, weak_checks: bool = False):
        self.split = split
        self.lie = lie                 # worker claims done without the checks passing
        self.weak_checks = weak_checks  # first check set is inadequate
        self.worker_steps: dict[str, int] = {}
        self.calls: list[str] = []
        self.dod_rounds: dict[str, int] = {}

    async def chat(self, model, messages, *, tools=None, json_mode=False, max_tokens=2000,
                   temperature=0.2, tag: str, ctx=None) -> LLMResult:
        self.calls.append(tag)
        node_id = getattr(ctx, "_current_node_id", "root")
        if ctx is not None and getattr(ctx, "budget", None):
            ctx.budget.spend(model, 400, 120, ctx.cfg)

        if tag == "planner":
            return _result(json.dumps(self._plan(node_id)), model=model)
        if tag in ("dod_writer", "dod_rewrite", "integration_checks"):
            return _result(json.dumps(self._checks(node_id, tag)), model=model)
        if tag == "worker":
            return _result(**self._worker(node_id))
        if tag == "meta_verifier":
            return _result(json.dumps(self._meta(ctx, node_id)), model=model)
        if tag == "verifier":
            return _result(json.dumps({
                "verdict": "DONE",
                "reasoning": "re-ran the checks myself; artifacts match the goal",
                "independent_findings": [],
            }), model=model)
        if tag == "summarize":
            return _result("storage.py exposes load/save/add/search; tests pass.", model=model)
        if tag == "drift_judge":
            return _result(json.dumps({"drift": 0, "reason": "on goal", "impossible": False}))
        return _result("{}", model=model)

    # --- scripted stages ---------------------------------------------------
    def _plan(self, node_id: str) -> dict:
        if self.split and node_id == "root":
            return {
                "decision": "SPLIT",
                "reasoning": "storage and tests are independent",
                "assumptions": ["notes live in ./notes.json", "ids are integers starting at 1"],
                "subgoals": [
                    {"goal": "write storage.py exposing load/save/add/search over notes.json",
                     "why": "everything else builds on it", "budget_share": 0.6},
                    {"goal": "write tests/test_storage.py covering case-insensitive search",
                     "why": "proves search behaviour", "budget_share": 0.4},
                ],
                "leaf_signal": "needs_decomposition",
            }
        return {"decision": "EXECUTE", "reasoning": "one small module",
                "assumptions": ["standard library only"], "subgoals": [],
                "leaf_signal": "small_file"}

    def _checks(self, node_id: str, tag: str) -> dict:
        self.dod_rounds[node_id] = self.dod_rounds.get(node_id, 0) + 1
        if self.weak_checks and tag == "dod_writer":
            # deliberately useless: passes whatever the worker does
            return {"checks": [
                {"id": "w1", "description": "something exists", "kind": "shell",
                 "command": "true"},
            ]}
        return {"checks": [
            {"id": "c1", "description": "storage.py round-trips a note", "kind": "shell",
             "command": "python3 -c \"import storage; storage.add('Buy MILK'); "
                        "assert storage.search('milk')\""},
            {"id": "c2", "description": "the test suite passes", "kind": "pytest",
             "command": "tests/test_storage.py"},
            {"id": "c3", "description": "negative: no stray secrets file is written",
             "kind": "shell", "command": "test -f secrets.txt", "negative": True},
        ]}

    def _worker(self, node_id: str) -> dict:
        step = self.worker_steps.get(node_id, 0)
        self.worker_steps[node_id] = step + 1
        if step == 0:
            return {"tool_calls": [
                _tool_call("t1", "write_file", {"path": "storage.py", "content": STORAGE}),
            ]}
        if step == 1:
            return {"tool_calls": [
                _tool_call("t2", "write_file", {"path": "tests/test_storage.py",
                                                "content": TEST_FILE}),
            ]}
        if step == 2 and not self.lie:
            return {"tool_calls": [_tool_call("t3", "run_checks", {})]}
        return {"tool_calls": [
            _tool_call("t4", "report_done", {"status": "done", "summary": "wrote storage + tests"}),
        ]}

    def _meta(self, ctx, node_id: str) -> dict:
        node = ctx.nodes.get(node_id)
        checks = node.dod if node else []
        if self.weak_checks and self.dod_rounds.get(node_id, 0) == 1:
            return {"checks": [{"id": c.id, "meta_verified": False,
                                "reason": "passes regardless of whether the goal is met"}
                               for c in checks],
                    "uncovered_aspects": ["nothing checks that notes can be stored at all"],
                    "verdict_hint": "INADEQUATE"}
        return {"checks": [{"id": c.id, "meta_verified": True, "reason": "exercises the goal"}
                           for c in checks],
                "uncovered_aspects": [], "verdict_hint": "ADEQUATE"}


@pytest.fixture
def fake(monkeypatch):
    def install(**kw) -> FakeModels:
        f = FakeModels(**kw)
        monkeypatch.setattr(llm, "chat", f.chat)
        for mod_name in ("planner", "dod_writer", "worker", "verifier", "solver", "checks",
                         "monitor"):
            mod = __import__(f"done.{mod_name}", fromlist=["llm"])
            if hasattr(mod, "llm"):
                monkeypatch.setattr(mod.llm, "chat", f.chat)
        return f
    return install


async def test_execute_leaf_writes_real_files_and_passes_real_checks(ctx, node_factory, fake):
    f = fake(split=False)
    node = node_factory(depth=ctx.cfg.MAX_DEPTH, model="openai/gpt-oss-120b",
                        goal="write storage.py with load/save/add/search over notes.json")
    out = await solver.solve(node, ctx)

    assert out.status == "done"
    assert (ctx.sandbox.root / "storage.py").exists(), "the worker's writes really landed"
    assert all(c.passed for c in out.dod), "every check really ran and really passed"
    assert out.verification.verdict == "DONE"
    assert family(out.verification.verifier_model) != family(out.model)
    assert "pytest" in [c.kind for c in out.dod]


async def test_split_lights_up_green_from_the_leaves(ctx, node_factory, fake):
    f = fake(split=True)
    root = await solver.solve(node_factory(model="deepseek-ai/DeepSeek-V4-Pro"), ctx)

    assert root.status == "done"
    assert root.decision == "SPLIT"
    assert [ctx.nodes[c].status for c in root.children] == ["done", "done"]
    assert root.dod, "the parent wrote its own integration checks"
    assert all(c.passed for c in root.dod)
    # the root's assumptions reached its children
    assert "notes.json" in ctx.nodes["root.1"].ancestors_context
    # children are cheaper models than the root
    assert ctx.nodes["root.1"].model == ctx.cfg.model_for_depth(1)
    assert "integration_checks" in f.calls


async def test_a_worker_that_claims_done_without_running_checks_is_still_verified(
        ctx, node_factory, fake):
    """The worker skips run_checks and asserts success. The verifier runs them anyway."""
    f = fake(split=False, lie=True)
    node = node_factory(depth=ctx.cfg.MAX_DEPTH, model="openai/gpt-oss-120b")
    out = await solver.solve(node, ctx)
    assert "verifier" in f.calls
    # the verdict rests on checks the verifier ran itself, not on the worker's claim
    assert out.verification.checks_total == len(out.dod)
    assert all(c.passed for c in out.dod)
    assert out.status == "done"


async def test_inadequate_checks_are_rewritten_before_a_verdict_sticks(ctx, node_factory, fake):
    f = fake(split=False, weak_checks=True)
    node = node_factory(depth=ctx.cfg.MAX_DEPTH, model="openai/gpt-oss-120b")
    out = await solver.solve(node, ctx)

    assert "dod_rewrite" in f.calls, "the useless check set was sent back to be rewritten"
    assert out.dod_rewrites == 1
    assert [c.id for c in out.dod] != ["w1"], "the replacement checks are in force"
    assert out.status == "done"
    assert all(c.meta_verified for c in out.dod)


async def test_the_trace_is_complete_enough_to_replay(ctx, node_factory, fake):
    fake(split=True)
    await solver.solve(node_factory(model="deepseek-ai/DeepSeek-V4-Pro"), ctx)
    lines = [json.loads(l) for l in (ctx.run_dir / "events.jsonl").read_text().splitlines()]
    types = {l["type"] for l in lines}
    for required in {"plan", "split", "dod", "tool_call", "run_checks", "meta_verify",
                     "verify_start", "verify_result", "node_update"}:
        assert required in types, f"the trace never recorded {required}"
    tree = json.loads((ctx.run_dir / "tree.json").read_text())
    assert set(tree["nodes"]) == {"root", "root.1", "root.2"}
    nodes, _ = __import__("done.report", fromlist=["load_run"]).load_run(ctx.run_dir)
    assert nodes["root"].status == "done"
