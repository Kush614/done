"""Prompts live on disk as .md so they can be edited (and, in P1, rewritten by the agent itself)."""

from __future__ import annotations

import re
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent
_VAR = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def load(name: str) -> str:
    path = PROMPT_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"no prompt named {name!r} in {PROMPT_DIR}")
    return path.read_text()


def render(name: str, **vars: object) -> str:
    """Substitute {{var}} placeholders. An unknown placeholder renders empty, never crashes a run."""
    template = load(name)

    def sub(m: re.Match) -> str:
        value = vars.get(m.group(1), "")
        return "" if value is None else str(value)

    return _VAR.sub(sub, template)


def node_vars(node, cfg=None) -> dict:
    """The five variables every prompt in SPEC §4 receives."""
    return {
        "root_goal": node.root_goal,
        "goal": node.goal,
        "ancestors_context": node.ancestors_context or "(none — you are the root)",
        "depth": node.depth,
        "budget_tokens": node.budget_tokens,
    }
