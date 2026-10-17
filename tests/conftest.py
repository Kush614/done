from __future__ import annotations

import pytest

from done.config import Config
from done.context import RunContext
from done.llm import Budget
from done.models import Node
from done.sandbox import Sandbox
from done.trace import Trace


@pytest.fixture
def cfg() -> Config:
    c = Config()
    c.DRIFT_EVERY_N_STEPS = 10_000   # tests never call the LLM drift judge
    return c


@pytest.fixture
def ctx(tmp_path, cfg) -> RunContext:
    run_dir = tmp_path / "run"
    return RunContext(
        cfg=cfg,
        trace=Trace(run_dir, "test goal", 60_000),
        budget=Budget(total=60_000),
        sandbox=Sandbox(run_dir / "workspace", cfg),
        run_dir=run_dir,
        run_id="test",
    )


@pytest.fixture
def node_factory(cfg):
    def make(**kw) -> Node:
        base = dict(id="root", depth=0, goal="do the thing", root_goal="do the thing",
                    budget_tokens=60_000, model=cfg.tiers[1])
        base.update(kw)
        return Node(**base)
    return make
