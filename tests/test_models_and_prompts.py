"""`done models` maps a live catalogue onto our tiers, and no prompt may ship a hole in it."""

from __future__ import annotations

import re

import pytest

from done.cli import _pick_tiers
from done.config import Config, family
from done.prompts import PROMPT_DIR, load, node_vars, render

LIVE_SAMPLE = [
    "deepseek-ai/DeepSeek-V4-Pro", "deepseek-ai/DeepSeek-V4-Flash",
    "openai/gpt-oss-120b", "openai/gpt-oss-20b",
    "nvidia/Nemotron-3-Nano-30B-A3B", "nvidia/Nemotron-3-Super-120B-A12B",
    "google/gemma-4-31b-it", "zai/GLM-5.1", "meta-llama/Llama-3.3-70B-Instruct",
]


def test_tiers_put_the_strong_planner_at_the_root_and_a_real_worker_below():
    """Nano at the leaves was measured and rejected: 23-39k tokens per node, most checks
    failing, where Super finished the same work in 14k. The tiniest model keeps the job it is
    actually good at — scoring drift 0-10."""
    picked = _pick_tiers(LIVE_SAMPLE, Config())
    assert picked["tiers"]["0"] == "deepseek-ai/DeepSeek-V4-Pro"
    assert "Nemotron-3-Super" in picked["tiers"]["1"]
    assert "Nemotron-3-Super" in picked["tiers"]["2"]
    assert "Nano" in picked["drift_judge_model"]
    assert all(v in LIVE_SAMPLE for v in picked["tiers"].values())
    assert picked["drift_judge_model"] in LIVE_SAMPLE


def test_the_worker_tier_is_always_verified_by_another_family():
    picked = _pick_tiers(LIVE_SAMPLE, Config())
    cfg = Config()
    cfg.verifier_for_family = picked["verifier_for_family"]
    for depth, model in picked["tiers"].items():
        assert family(cfg.verifier_for(model)) != family(model), f"depth {depth} self-verifies"


def test_verifier_map_never_lets_a_family_verify_itself():
    picked = _pick_tiers(LIVE_SAMPLE, Config())
    for fam, verifier in picked["verifier_for_family"].items():
        assert family(verifier) != fam, f"{fam} → {verifier} is self-verification"


def test_a_sparse_catalogue_still_yields_a_usable_config():
    """Crusoe serving only two families must not break the independence guarantee."""
    sparse = ["openai/gpt-oss-120b", "google/gemma-4-31b-it"]
    picked = _pick_tiers(sparse, Config())
    assert set(picked["tiers"].values()) <= set(sparse)
    for fam in ("openai", "google"):
        assert family(picked["verifier_for_family"][fam]) != fam


def test_a_single_family_catalogue_is_reported_not_silently_accepted():
    """If only one family is live, cross-family verification is impossible — say so loudly."""
    cfg = Config()
    # a genuinely single-family catalogue: tiers, verifier map and fallback all one family
    cfg.verifier_for_family = {"openai": "openai/gpt-oss-20b"}
    cfg.tiers = {0: "openai/gpt-oss-120b", 1: "openai/gpt-oss-120b", 2: "openai/gpt-oss-20b"}
    cfg.fallback_model = "openai/gpt-oss-120b"
    with pytest.raises(ValueError):
        cfg.verifier_for("openai/gpt-oss-120b")


def test_a_contributor_family_can_never_be_chosen_as_the_verifier():
    """A rate limit can hand a node's work to the fallback model mid-run. The family that
    actually did the work must not then be the family that signs it off."""
    cfg = Config()
    # pretend nvidia was rate-limited and openai/gpt-oss did the work instead
    verifier = cfg.verifier_for("nvidia/NVIDIA-Nemotron-3-Super-120B-A12B",
                                forbid={"openai"})
    assert family(verifier) not in ("nvidia", "openai")
    chain = cfg.cross_family_fallbacks("nvidia/NVIDIA-Nemotron-3-Super-120B-A12B",
                                       forbid={"openai"})
    assert chain and all(family(m) not in ("nvidia", "openai") for m in chain)


ALL_PROMPTS = sorted(p.stem for p in PROMPT_DIR.glob("*.md"))


def test_we_have_the_prompts_the_spec_names():
    for required in ("planner", "worker", "dod_writer", "verifier", "meta_verifier", "drift_judge"):
        assert required in ALL_PROMPTS


@pytest.mark.parametrize("name", ALL_PROMPTS)
def test_every_prompt_renders_with_no_leftover_placeholder(name, node_factory):
    node = node_factory()
    filled = render(
        name,
        **node_vars(node),
        max_depth=3, max_children=5, workspace="- cli.py", checks="- c1 shell: true",
        old_checks="- c1", verifier_reasoning="too weak", child_reports="- [root.1] done",
        meta_results="- c1 meta_verified=True", check_results="- c1: PASS",
        recent_steps="1. write_file cli.py", rubric="is it readable", description="readable",
        files="--- README.md ---", verifier_model="zai/GLM-5.1", verdict="DONE",
        checks_passed=1, checks_total=1, findings="(none)", attempt=2,
        failing="- c1 FAILED", retry_block="",
    )
    leftover = re.findall(r"\{\{\s*\w+\s*\}\}", filled)
    assert not leftover, f"{name}.md has unfilled placeholders: {leftover}"
    assert len(filled) > 200


# the worker drives a tool loop; it finishes by calling report_done, not by returning a shape.
TOOL_LOOP_PROMPTS = {"worker", "worker_retry"}


@pytest.mark.parametrize("name", sorted(set(ALL_PROMPTS) - TOOL_LOOP_PROMPTS))
def test_every_returning_prompt_demands_a_concrete_output_shape(name):
    body = load(name).lower()
    assert "return only" in body or "return plain text" in body, \
        f"{name}.md never tells the model what to return"


@pytest.mark.parametrize("name", sorted(TOOL_LOOP_PROMPTS))
def test_tool_loop_prompts_name_the_tools_and_forbid_an_unchecked_claim(name):
    body = load(name).lower()
    assert "run_checks" in body, f"{name}.md never tells the worker to run its checks"
    if name == "worker":
        assert "report_done" in body
        assert "do not claim done" in body or "only when" in body or "done only when" in body


def test_json_prompts_name_their_schema():
    for name in ("planner", "dod_writer", "verifier", "meta_verifier", "drift_judge"):
        body = load(name)
        assert "{" in body and "}" in body, f"{name}.md shows no JSON schema"


def test_the_run_index_summarises_a_real_run(tmp_path):
    """The frontend reads this cache, so it must survive a half-written or interrupted run."""
    import json
    from done.index import build_index, summarize_run

    run = tmp_path / "120000-001"
    run.mkdir()
    (run / "tree.json").write_text(json.dumps({
        "root_goal": "do a thing",
        "budget_total": 1000,
        "elapsed_s": 12.5,
        "meta": {"tokens_used": 900, "cost_usd": 0.01, "counters": {"monitor_loop": 1}},
        "nodes": {
            "root": {"id": "root", "goal": "do a thing", "root_goal": "do a thing",
                     "status": "needs_human", "model": "nvidia/X", "children": ["root.1"],
                     "human_questions": ["which SMTP server should it use?", "none"],
                     "dod": [{"id": "c1", "description": "x", "passed": True}]},
            "root.1": {"id": "root.1", "parent_id": "root", "depth": 1, "goal": "sub",
                       "root_goal": "do a thing", "status": "done", "model": "nvidia/X",
                       "verification": {"verifier_model": "openai/Y", "verdict": "DONE",
                                        "checks_passed": 1, "checks_total": 1},
                       "dod": [{"id": "c1", "description": "y", "passed": True}]},
        },
    }))
    row = summarize_run(run)
    assert row["verdict"] == "NEEDS_HUMAN"
    assert row["checks_passed"] == 2 and row["checks_total"] == 2
    assert row["nodes"] == 2 and row["max_depth"] == 1
    assert row["human_questions"] == ["which SMTP server should it use?"], "'none' is not a question"
    assert set(row["families"]) == {"nvidia", "openai"}
    assert row["monitor"]["loops"] == 1

    # a directory with no tree.json, and an unreadable one, must not break the index
    (tmp_path / "empty").mkdir()
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "tree.json").write_text("{not json")
    idx = build_index(tmp_path)
    assert idx["totals"]["runs"] == 1
    assert idx["runs"][0]["id"] == "120000-001"


def test_an_interrupted_run_reports_incomplete(tmp_path):
    import json
    from done.index import summarize_run
    run = tmp_path / "130000-002"
    run.mkdir()
    (run / "tree.json").write_text(json.dumps({
        "root_goal": "g", "nodes": {"root": {"id": "root", "goal": "g", "root_goal": "g",
                                             "status": "planning", "model": "a/b"}}}))
    assert summarize_run(run)["verdict"] == "INCOMPLETE"
