"""The one object every agent in a run shares."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .llm import Budget
from .models import Node
from .sandbox import Sandbox
from .trace import Trace

# Siblings run concurrently under asyncio.gather. A single attribute would have them
# overwriting each other's "current node", so every LLM call charged whichever sibling
# happened to run last. A ContextVar is copied per task, so each branch of the tree keeps
# its own value. (DECISIONS 15:00)
_CURRENT_NODE: ContextVar[str | None] = ContextVar("done_current_node", default=None)


@dataclass
class RunContext:
    cfg: Config
    trace: Trace
    budget: Budget
    sandbox: Sandbox
    run_dir: Path
    run_id: str
    nodes: dict[str, Node] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)

    @property
    def _current_node_id(self) -> str | None:
        return _CURRENT_NODE.get()

    def enter(self, node: Node) -> None:
        """Mark a node current so trace events and token charges attribute to it."""
        _CURRENT_NODE.set(node.id)
        self.nodes[node.id] = node

    def bump(self, key: str, n: int = 1) -> int:
        self.counters[key] = self.counters.get(key, 0) + n
        return self.counters[key]

    def charge(self, node: Node, before_tokens: int, before_cost: float) -> None:
        """Attribute budget movement since a mark to one node."""
        node.tokens_used += self.budget.used - before_tokens
        node.cost_usd += self.budget.cost_usd - before_cost
