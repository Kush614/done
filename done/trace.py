"""Append-only event log + a tree.json snapshot the viewer polls."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .models import Node


class Trace:
    def __init__(self, run_dir: Path, root_goal: str, budget_total: int):
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = run_dir / "events.jsonl"
        self.tree_path = run_dir / "tree.json"
        self.root_goal = root_goal
        self.budget_total = budget_total
        self.started = time.time()
        self.nodes: dict[str, Node] = {}
        self._meta: dict[str, Any] = {}
        self.events_path.touch()

    # --- events -------------------------------------------------------------
    def event(self, type: str, node_id: str | None = None, **payload: Any) -> None:
        rec = {
            "ts": round(time.time() - self.started, 3),
            "wall": time.strftime("%H:%M:%S"),
            "node_id": node_id,
            "type": type,
            "payload": payload,
        }
        with self.events_path.open("a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")

    def llm_call(self, *, tag: str, model: str, usage: dict, latency_ms: int,
                 node_id: str | None, **extra: Any) -> None:
        self.event("llm_call", node_id, tag=tag, model=model, usage=usage,
                   latency_ms=latency_ms, **extra)

    # --- tree snapshot ------------------------------------------------------
    def node_update(self, node: Node) -> None:
        self.nodes[node.id] = node
        self.event("node_update", node.id, status=node.status, decision=node.decision,
                   depth=node.depth, model=node.model, tokens_used=node.tokens_used)
        self.write_tree()

    def set_meta(self, **kw: Any) -> None:
        self._meta.update(kw)
        self.write_tree()

    def _recompute_subtrees(self) -> None:
        """Subtree totals, bottom-up, fresh on every write. A snapshot taken when the children
        finished went stale the moment the parent paid for its own verification."""
        def total(nid: str, seen: frozenset[str]) -> tuple[int, float]:
            node = self.nodes.get(nid)
            if node is None or nid in seen:
                return 0, 0.0
            seen = seen | {nid}
            tok, cost = node.tokens_used, node.cost_usd
            for cid in node.children:
                ct, cc = total(cid, seen)
                tok += ct
                cost += cc
            node.subtree_tokens = tok
            node.subtree_cost_usd = round(cost, 6)
            return tok, cost

        for nid in list(self.nodes):
            if self.nodes[nid].parent_id is None:
                total(nid, frozenset())

    def write_tree(self) -> None:
        self._recompute_subtrees()
        payload = {
            "root_goal": self.root_goal,
            "budget_total": self.budget_total,
            "elapsed_s": round(time.time() - self.started, 1),
            "meta": self._meta,
            "nodes": {nid: json.loads(n.model_dump_json()) for nid, n in self.nodes.items()},
        }
        tmp = self.tree_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self.tree_path)  # atomic, so the polling viewer never reads a half file
