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


def test_tiers_are_cheap_at_the_leaves_and_strong_at_the_root():
    picked = _pick_tiers(LIVE_SAMPLE, Config())
    assert picked["tiers"]["0"] == "deepseek-ai/DeepSeek-V4-Pro"
    assert picked["tiers"]["1"] == "openai/gpt-oss-120b"
    assert "Nano" in picked["tiers"]["2"]
    assert "Nano" in picked["drift_judge_model"]
    assert all(v in LIVE_SAMPLE for v in picked["tiers"].values())


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
    cfg.verifier_for_family = {"openai": "openai/gpt-oss-20b"}
    cfg.fallback_model = "openai/gpt-oss-120b"
    with pytest.raises(ValueError):
        cfg.verifier_for("openai/gpt-oss-120b")


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
