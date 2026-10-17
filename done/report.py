"""The terminal rendering: a live tree while it runs, and the final report (SPEC §10)."""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from .config import Config, family
from .models import Node

STATUS_STYLE = {
    "pending": "grey50",
    "planning": "blue",
    "executing": "magenta",
    "verifying": "yellow",
    "replanning": "cyan",
    "done": "bold green",
    "failed": "bold red",
    "stopped": "red",
    "needs_human": "bold dark_orange",
}
STATUS_MARK = {
    "pending": "·", "planning": "◌", "executing": "◍", "verifying": "◎",
    "replanning": "↻", "done": "✓", "failed": "✗", "stopped": "■", "needs_human": "?",
}


def node_line(node: Node) -> Text:
    style = STATUS_STYLE.get(node.status, "white")
    t = Text()
    t.append(f"{STATUS_MARK.get(node.status, '·')} ", style=style)
    t.append(f"{node.id} ", style="bold")
    t.append(f"[{node.status}]", style=style)
    t.append(f" {node.model.split('/')[-1][:26]}", style="grey62")
    if node.dod:
        passed = sum(1 for c in node.dod if c.passed)
        t.append(f"  checks {passed}/{len(node.dod)}", style="grey70")
    if node.verification:
        v = node.verification
        t.append(f"  ⇄ {v.verifier_model.split('/')[-1][:18]}", style="cyan")
    if node.flags:
        kinds = ",".join(sorted({f.kind for f in node.flags}))
        t.append(f"  ⚑{kinds}", style="bold red")
    if node.tokens_used:
        t.append(f"  {node.tokens_used:,}tok", style="grey50")
    t.append(f"\n    {node.goal[:96]}", style="grey74")
    if node.events:
        t.append(f"\n    ↳ {node.events[-1][:96]}", style="grey42")
    return t


def build_tree(nodes: dict[str, Node], root_id: str = "root") -> Tree:
    root = nodes.get(root_id)
    tree = Tree(node_line(root) if root else Text("(no root yet)"))

    def add(parent_widget, node: Node) -> None:
        for cid in node.children:
            child = nodes.get(cid)
            if child is None:
                continue
            widget = parent_widget.add(node_line(child))
            add(widget, child)

    if root:
        add(tree, root)
    return tree


def live_panel(nodes: dict[str, Node], budget, cfg: Config, root_goal: str, elapsed: float) -> Panel:
    header = Text()
    header.append("GOAL  ", style="bold")
    header.append(root_goal[:110])
    header.append(f"\nSPEND {budget.used:,}/{budget.total:,} tokens  ", style="grey62")
    header.append(f"${budget.cost_usd:.4f}  ", style="green")
    header.append(f"{elapsed:.0f}s", style="grey62")
    return Panel(Group(header, Text(""), build_tree(nodes)),
                 title="done — recursive verifier court", border_style="grey37")


def final_report(nodes: dict[str, Node], budget, cfg: Config, counters: dict,
                 root_goal: str, run_dir: Path) -> Group:
    root = nodes.get("root")
    verdict = {"done": "DONE", "stopped": "STOPPED", "needs_human": "NEEDS_HUMAN",
               "failed": "FAILED"}.get(root.status if root else "failed", "INCOMPLETE")

    all_checks = [c for n in nodes.values() for c in n.dod]
    passed = sum(1 for c in all_checks if c.passed)
    verifiers = {n.verification.verifier_model for n in nodes.values() if n.verification}
    families = {family(m) for m in verifiers}

    assumptions = [a for n in nodes.values() for a in n.assumptions]
    loops = counters.get("monitor_loop", 0)
    drifts = counters.get("monitor_drift", 0)

    t = Table.grid(padding=(0, 2))
    t.add_column(style="bold grey62")
    t.add_column()
    t.add_row("GOAL", root_goal)
    t.add_row("VERDICT", Text(verdict, style=STATUS_STYLE.get(root.status if root else "failed", "white")))
    t.add_row("Checks", f"{passed}/{len(all_checks)} passed "
                        f"(verified by {len(verifiers)} independent models "
                        f"across {len(families)} families)")
    if assumptions:
        shown = "\n".join(f"• {a}" for a in assumptions[:8])
        t.add_row("Assumptions made", shown + ("\n…" if len(assumptions) > 8 else ""))
    t.add_row("Monitor", f"{loops} loop(s) caught, {drifts} drift flag(s), "
                         f"{counters.get('checks_rewritten', 0)} check-set(s) rewritten as inadequate")
    root_model = cfg.model_for_depth(0)
    baseline = budget.naive_baseline_usd(root_model, cfg)
    t.add_row("Tokens", f"{budget.used:,} · Cost: ${budget.cost_usd:.4f} · "
                        f"vs all-{root_model.split('/')[-1]} baseline ${baseline:.4f} "
                        f"({_ratio(baseline, budget.cost_usd)})")
    per_model = "\n".join(
        f"• {m.split('/')[-1][:34]:34s} {int(d['tokens']):>7,}tok  ${d['cost_usd']:.4f}  "
        f"{int(d['calls'])} calls"
        for m, d in sorted(budget.by_model.items(), key=lambda kv: -kv[1]["cost_usd"])
    )
    if per_model:
        t.add_row("By model", per_model)
    human = [n for n in nodes.values() if n.status == "needs_human"]
    t.add_row("Human needed", "no" if not human else
              "YES — " + (human[0].final_report or "").split("\n")[-1][:140])
    t.add_row("Trace", f"{run_dir}/tree.json  ·  {run_dir}/events.jsonl")

    body: list = [build_tree(nodes), Text(""), t]
    if root and root.final_report:
        body += [Text(""), Panel(Text(root.final_report), title="root report",
                                 border_style="grey37")]
    return Group(*body)


def _ratio(baseline: float, actual: float) -> str:
    if actual <= 0:
        return "n/a"
    return f"{baseline / actual:.1f}× cheaper"


def load_run(run_dir: Path) -> tuple[dict[str, Node], dict]:
    data = json.loads((run_dir / "tree.json").read_text())
    nodes = {nid: Node.model_validate(n) for nid, n in data["nodes"].items()}
    return nodes, data
