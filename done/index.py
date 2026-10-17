"""Cache every run into one manifest the frontend can read.

Without this the viewer had to scrape `python -m http.server`'s directory listing, which does
not exist on a static host and says nothing about what a run contains. `runs/index.json` is
written after every run and by `done index`.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .config import family
from .models import Node
from .planner import _is_real_question

INDEX_NAME = "index.json"


def summarize_run(run_dir: Path) -> dict[str, Any] | None:
    """One row of the manifest. Returns None if the directory holds no readable run."""
    tree_path = run_dir / "tree.json"
    if not tree_path.exists():
        return None
    try:
        data = json.loads(tree_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    raw_nodes = data.get("nodes") or {}
    nodes: dict[str, Node] = {}
    for nid, payload in raw_nodes.items():
        try:
            nodes[nid] = Node.model_validate(payload)
        except Exception:  # noqa: BLE001 - a half-written node must not lose the whole run
            continue

    root = nodes.get("root")
    meta = data.get("meta") or {}
    checks = [c for n in nodes.values() for c in n.dod]
    verifiers = sorted({n.verification.verifier_model for n in nodes.values() if n.verification})
    workers = sorted({n.model for n in nodes.values() if n.model})
    # Filter here rather than rewriting recorded runs: older traces predate the fix that
    # rejects "NEEDS HUMAN: none", and a run's own files stay exactly as they were written.
    questions: list[str] = []
    for n in nodes.values():
        questions += [q for q in n.human_questions
                      if q not in questions and _is_real_question(q)]

    tokens = meta.get("tokens_used") or sum(n.tokens_used for n in nodes.values())
    cost = meta.get("cost_usd")
    if cost is None:
        cost = round(sum(n.cost_usd for n in nodes.values()), 6)

    status = root.status if root else "unknown"
    # a run that was interrupted leaves its root mid-flight; say so rather than reporting
    # the phase it happened to be in
    verdict = {"done": "DONE", "stopped": "STOPPED", "needs_human": "NEEDS_HUMAN",
               "failed": "FAILED"}.get(status, "INCOMPLETE")

    rel = run_dir.name
    if run_dir.parent.name == "demo-final":
        rel = f"demo-final/{run_dir.name}"

    return {
        "id": rel,
        "name": run_dir.name,
        "preserved": run_dir.parent.name == "demo-final",
        "goal": data.get("root_goal", ""),
        "status": status,
        "verdict": verdict,
        "checks_passed": sum(1 for c in checks if c.passed),
        "checks_total": len(checks),
        "nodes": len(nodes),
        "leaves": sum(1 for n in nodes.values() if not n.children),
        "max_depth": max((n.depth for n in nodes.values()), default=0),
        "split": bool(root and root.decision == "SPLIT"),
        "tokens": tokens,
        "cost_usd": round(float(cost or 0), 6),
        "elapsed_s": data.get("elapsed_s", 0),
        "worker_models": workers,
        "verifier_models": verifiers,
        "families": sorted({family(m) for m in workers + verifiers}),
        "human_questions": questions,
        "assumptions": [a for n in nodes.values() for a in n.assumptions][:12],
        "monitor": {
            "loops": (meta.get("counters") or {}).get("monitor_loop", 0),
            "drift": (meta.get("counters") or {}).get("monitor_drift", 0),
            "checks_rewritten": (meta.get("counters") or {}).get("checks_rewritten", 0),
            "checks_falsified": (meta.get("counters") or {}).get("checks_falsified", 0),
        },
        "final_report": (root.final_report if root else None),
        "mtime": int(tree_path.stat().st_mtime),
    }


def build_index(runs_root: Path) -> dict[str, Any]:
    """Scan runs/ and runs/demo-final/ and cache a summary of every run."""
    rows: list[dict[str, Any]] = []
    for d in sorted(runs_root.iterdir()) if runs_root.exists() else []:
        if not d.is_dir():
            continue
        if d.name == "demo-final":
            for sub in sorted(d.iterdir()):
                if sub.is_dir() and (row := summarize_run(sub)):
                    rows.append(row)
            continue
        if row := summarize_run(d):
            rows.append(row)

    rows.sort(key=lambda r: (not r["preserved"], -r["mtime"]))
    totals = {
        "runs": len(rows),
        "tokens": sum(r["tokens"] for r in rows),
        "cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
        "checks_passed": sum(r["checks_passed"] for r in rows),
        "checks_total": sum(r["checks_total"] for r in rows),
        "verdicts": {v: sum(1 for r in rows if r["verdict"] == v)
                     for v in sorted({r["verdict"] for r in rows})},
        "families": sorted({f for r in rows for f in r["families"]}),
    }
    return {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "totals": totals, "runs": rows}


def write_index(runs_root: Path) -> Path:
    payload = build_index(runs_root)
    path = runs_root / INDEX_NAME
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path
