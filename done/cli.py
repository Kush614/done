"""done — CLI. `done models` first, then `done run "<goal>"`, then `done replay <dir>`."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.live import Live
from rich.table import Table

from . import llm, report, solver
from .config import REPO_ROOT, Config, family, load_config, save_models_json
from .context import RunContext
from .llm import Budget
from .models import Node
from .sandbox import Sandbox
from .trace import Trace

console = Console()


# --------------------------------------------------------------------------- models
async def _models(write: bool) -> int:
    live = await llm.list_models()
    ids = sorted(m.get("id", "?") for m in live)
    table = Table(title=f"{len(ids)} models live on Crusoe Managed Inference", title_style="bold")
    table.add_column("#", style="grey50")
    table.add_column("model id")
    table.add_column("family", style="cyan")
    for i, mid in enumerate(ids, 1):
        table.add_row(str(i), mid, family(mid))
    console.print(table)

    cfg = Config()
    chosen = _pick_tiers(ids, cfg)
    console.print("\n[bold]tier assignment[/bold]")
    for depth, mid in sorted(chosen["tiers"].items()):
        label = {"0": "root planner", "1": "mid planner/worker",
                 "2": "leaves"}.get(str(depth), f"depth {depth}")
        console.print(f"  depth {depth} ({label}): [bold]{mid}[/bold]")
    console.print(f"  drift judge: {chosen['drift_judge_model']}")
    console.print(f"  fallback:    {chosen['fallback_model']}")
    console.print("\n[bold]cross-family verifier map[/bold]")
    for fam, v in sorted(chosen["verifier_for_family"].items()):
        flag = "" if family(v) != fam else "  [red]SAME FAMILY — BAD[/red]"
        console.print(f"  {fam:10s} → {v}{flag}")

    if write:
        path = save_models_json(chosen)
        console.print(f"\n[green]wrote {path.relative_to(REPO_ROOT)}[/green] "
                      f"— config.py reads it from now on")
    return 0


# preference order within each tier, best first. Matched by substring against live ids.
ROOT_PREFS = ["deepseek-v4-pro", "deepseek-v3", "glm-5", "glm-4", "kimi", "deepseek", "llama-3.3-70b"]
MID_PREFS = ["nemotron-3-super", "nemotron-super", "gpt-oss-120b", "gemma-4", "llama-3.3-70b"]
LEAF_PREFS = ["nemotron-3-super", "nemotron-super", "gpt-oss-120b", "gemma-4", "gemma-3"]
# the drift judge only scores 0-10, so the tiniest model is right for it
JUDGE_PREFS = ["nemotron-3-nano", "nemotron-nano", "nemotron-3.5-lightning", "gpt-oss-20b",
               "gemma-4", "gpt-oss-120b"]


def _pick_tiers(ids: list[str], cfg: Config) -> dict:
    """Map whatever Crusoe actually serves onto our three tiers and a cross-family verifier map."""
    def pick(prefs: list[str], fallback: str) -> str:
        low = {i.lower(): i for i in ids}
        for want in prefs:
            for lid, orig in low.items():
                if want in lid:
                    return orig
        return fallback if fallback in ids else (ids[0] if ids else fallback)

    tiers = {
        0: pick(ROOT_PREFS, cfg.tiers[0]),
        1: pick(MID_PREFS, cfg.tiers[1]),
        2: pick(LEAF_PREFS, cfg.tiers[2]),
    }
    by_family: dict[str, list[str]] = {}
    for mid in ids:
        by_family.setdefault(family(mid), []).append(mid)

    def best_of(fam: str) -> str:
        """Cheapest model of a family that we would actually trust to follow a JSON schema."""
        pool = by_family[fam]
        for want in MID_PREFS + LEAF_PREFS + ROOT_PREFS:
            for mid in pool:
                if want in mid.lower():
                    return mid
        return sorted(pool)[0]

    # Start from the hand-picked pairs and keep every one whose target is actually live; only
    # synthesize a verifier for a family we had no pairing for. Pure rotation was worse: it
    # handed every leaf to whichever model happened to land in the slot. (DECISIONS.md)
    verifier_for_family: dict[str, str] = {
        fam: v for fam, v in cfg.verifier_for_family.items()
        if v in ids and family(v) != fam and fam in by_family
    }
    for fam in sorted(by_family):
        if fam in verifier_for_family:
            continue
        for want_fam in ("openai", "google", "nvidia", "deepseek-ai", "zai-org", "qwen"):
            if want_fam != fam and want_fam in by_family:
                verifier_for_family[fam] = best_of(want_fam)
                break
        else:
            others = [f for f in by_family if f != fam]
            if others:
                verifier_for_family[fam] = best_of(sorted(others)[0])

    fallback = pick(["llama-3.3-70b", "gpt-oss-120b", "gemma"], cfg.fallback_model)
    drift = pick(JUDGE_PREFS, cfg.drift_judge_model)
    return {
        "probed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "live_models": ids,
        "tiers": {str(k): v for k, v in tiers.items()},
        "verifier_for_family": verifier_for_family,
        "fallback_model": fallback,
        "drift_judge_model": drift,
    }


# --------------------------------------------------------------------------- run
async def _run(goal: str, budget_tokens: int, out: Path, max_depth: int | None,
               quiet: bool) -> int:
    cfg = load_config()
    if max_depth is not None:
        cfg.MAX_DEPTH = max_depth

    run_id = time.strftime("%H%M%S") + f"-{abs(hash(goal)) % 997:03d}"
    run_dir = out / run_id
    (run_dir / "workspace").mkdir(parents=True, exist_ok=True)
    (run_dir / "goal.txt").write_text(goal + "\n")

    trace = Trace(run_dir, goal, budget_tokens)
    ctx = RunContext(
        cfg=cfg,
        trace=trace,
        budget=Budget(total=budget_tokens),
        sandbox=Sandbox(run_dir / "workspace", cfg),
        run_dir=run_dir,
        run_id=run_id,
    )
    trace.set_meta(run_id=run_id, tiers={str(k): v for k, v in cfg.tiers.items()},
                   max_depth=cfg.MAX_DEPTH, verifier_map=cfg.verifier_for_family)
    trace.event("run_start", None, goal=goal, budget=budget_tokens,
                tiers={str(k): v for k, v in cfg.tiers.items()})

    root = Node(id="root", parent_id=None, depth=0, goal=goal, root_goal=goal,
                budget_tokens=budget_tokens, model=cfg.model_for_depth(0))

    console.print(f"[grey62]run {run_id} · workspace {run_dir / 'workspace'}[/grey62]")
    console.print(f"[grey62]viewer: python3 -m http.server 8777 → "
                  f"http://localhost:8777/viewer/?run={run_dir.name}[/grey62]\n")

    task = asyncio.create_task(solver.solve(root, ctx))
    started = time.time()
    if quiet:
        await task
    else:
        with Live(console=console, refresh_per_second=4, transient=False) as live:
            while not task.done():
                live.update(report.live_panel(trace.nodes, ctx.budget, cfg, goal,
                                              time.time() - started))
                await asyncio.sleep(0.25)
            live.update(report.live_panel(trace.nodes, ctx.budget, cfg, goal,
                                          time.time() - started))
    try:
        await task
    except Exception as exc:  # noqa: BLE001 - print the report even on a crash
        console.print(f"[red]run crashed: {type(exc).__name__}: {exc}[/red]")
        trace.event("run_crash", None, error=str(exc)[:600])

    trace.set_meta(counters=ctx.counters, cost_usd=round(ctx.budget.cost_usd, 6),
                   tokens_used=ctx.budget.used, by_model=ctx.budget.by_model,
                   elapsed_s=round(time.time() - started, 1), finished=True)
    trace.event("run_end", None, status=root.status, tokens=ctx.budget.used,
                cost_usd=round(ctx.budget.cost_usd, 6), counters=ctx.counters)

    console.rule("[bold]final report")
    console.print(report.final_report(trace.nodes, ctx.budget, cfg, ctx.counters, goal, run_dir))
    (run_dir / "report.json").write_text(json.dumps({
        "goal": goal,
        "verdict": root.status,
        "tokens": ctx.budget.used,
        "cost_usd": round(ctx.budget.cost_usd, 6),
        "counters": ctx.counters,
        "final_report": root.final_report,
        "assumptions": [a for n in trace.nodes.values() for a in n.assumptions],
    }, indent=2))
    return 0 if root.status == "done" else (3 if root.status == "needs_human" else 1)


# --------------------------------------------------------------------------- replay
def _replay(run_dir: Path) -> int:
    if not (run_dir / "tree.json").exists():
        console.print(f"[red]no tree.json in {run_dir}[/red]")
        return 1
    nodes, data = report.load_run(run_dir)
    cfg = load_config()
    meta = data.get("meta", {})
    budget = Budget(total=data.get("budget_total", 0), used=meta.get("tokens_used", 0),
                    cost_usd=meta.get("cost_usd", 0.0))
    budget.by_model = meta.get("by_model", {})
    console.print(report.final_report(nodes, budget, cfg, meta.get("counters", {}),
                                      data.get("root_goal", "?"), run_dir))
    return 0


# --------------------------------------------------------------------------- entry
def main(argv: list[str] | None = None) -> int:
    load_dotenv(REPO_ROOT / ".env")
    load_dotenv()
    parser = argparse.ArgumentParser(prog="done", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="solve a goal")
    p_run.add_argument("goal", help="the one-line goal, or @path/to/file.txt")
    p_run.add_argument("--budget", type=int, default=250_000,
                   help="token budget for the whole run (~$0.03 at these tiers)")
    p_run.add_argument("--max-depth", type=int, default=None)
    p_run.add_argument("--out", type=Path, default=REPO_ROOT / "runs")
    p_run.add_argument("--quiet", action="store_true", help="no live tree (for scripted runs)")

    p_replay = sub.add_parser("replay", help="print a finished run, no LLM calls")
    p_replay.add_argument("run_dir", type=Path)

    p_models = sub.add_parser("models", help="list live Crusoe models and write models.json")
    p_models.add_argument("--no-write", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "models":
            return asyncio.run(_models(write=not args.no_write))
        if args.cmd == "replay":
            return _replay(args.run_dir)
        goal = args.goal
        if goal.startswith("@"):
            goal = Path(goal[1:]).read_text().strip()
        return asyncio.run(_run(goal, args.budget, args.out, args.max_depth, args.quiet))
    except KeyboardInterrupt:
        console.print("\n[yellow]interrupted[/yellow]")
        return 130
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        return 2


if __name__ == "__main__":
    sys.exit(main())
