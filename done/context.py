"""The one object every agent in a run shares."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .llm import Budget
from .models import Node
from .sandbox import Sandbox
from .trace import Trace


@dataclass
class RunContext:
    cfg: Config
    trace: Trace
    budget: Budget
    sandbox: Sandbox
    run_dir: Path
    run_id: str
    nodes: dict[str, Node] = field(default_factory=dict)
    _current_node_id: str | None = None
    counters: dict[str, int] = field(default_factory=dict)

    def enter(self, node: Node) -> None:
        """Mark a node current so trace events and token charges attribute to it."""
        self._current_node_id = node.id
        self.nodes[node.id] = node

    def bump(self, key: str, n: int = 1) -> int:
        self.counters[key] = self.counters.get(key, 0) + n
        return self.counters[key]

    def charge(self, node: Node, before_tokens: int, before_cost: float) -> None:
        """Attribute budget movement since a mark to one node."""
        node.tokens_used += self.budget.used - before_tokens
        node.cost_usd += self.budget.cost_usd - before_cost
