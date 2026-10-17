# done — the Recursive Verifier Court

**Track: "Done Means Done" · Recursive Agents Hackathon · Oct 17, 2026 · built on Crusoe Managed Inference**

> An agent gets a one-line goal and a token budget. It either does the work and writes an
> **executable definition of done**, or it **splits the goal and calls itself** on each piece with a
> smaller budget and a cheaper model. No node may report "done" until an **independent verifier from
> a different model family** has re-run its checks from scratch. Nodes notice when they are looping
> or drifting, and choose: continue, re-plan, stop honestly, or — rarely — ask a human.

The failure this attacks: agents fill the holes in a vague goal with silent assumptions, then
confidently report success. Here the assumptions are printed, the checks are written *before* the
work starts, and the agent that did the work is never the agent that decides whether it is done.

```
                       root · DeepSeek (depth 0)  ──── verified by GLM  (different family)
                        │ SPLIT, budget 60k − 15% reserve
      ┌─────────────────┼─────────────────┬──────────────────┐
  storage           cli commands        tests            README
  gpt-oss-120b      gpt-oss-120b     Nemotron Nano     Nemotron Nano
      │                 │                 │                 │
  verified by       verified by       verified by      verified by
   Gemma             Gemma             gpt-oss          gpt-oss
```

## Run it

```bash
cp .env.example .env            # put your CRUSOE_API_KEY in it
uv sync

uv run done models              # FIRST: what Crusoe actually serves → writes models.json
uv run done run "@tasks/demo_01_cli_tool.txt" --budget 60000
uv run done replay runs/<id>    # re-print a finished run, zero LLM calls
uv run pytest -q                # 30 offline tests, ~1s
```

Live tree viewer (optional, static, no build step):

```bash
python3 -m http.server 8777     # from the repo root
open http://localhost:8777/viewer/      # picks up the newest run automatically
```

## How it works

| Piece | File | What it does |
|---|---|---|
| recursive core | `done/solver.py` | `solve(node)` → EXECUTE or SPLIT; siblings run in parallel via `asyncio.gather` |
| planner | `done/planner.py` | decides, and **must declare its assumptions** — they are carried to children and printed in the report |
| definition of done | `done/dod_writer.py` | 3–7 runnable checks, written **before** the work; ≥1 must be *negative*; rewrites them when a verifier calls them inadequate |
| worker | `done/worker.py` | monitored tool loop; may only call `report_done(status="done")` once `run_checks` is green |
| verifier | `done/verifier.py` | **different model family**, never sees the worker's transcript, meta-verifies the checks, then re-runs every one itself |
| monitor | `done/monitor.py` | loop/oscillation/no-progress heuristics (free), drift judge (Nemotron Nano) only on trigger |
| sandbox | `done/sandbox.py` | per-run workspace, path-jailed, no network, 30s command timeout |
| trace + viewer | `done/trace.py`, `viewer/index.html` | append-only `events.jsonl` + atomically rewritten `tree.json`, polled every 500 ms |

### Why it is actually recursive

1. `solve()` calls itself on sub-goals, with a smaller budget and a cheaper model each level down.
2. The **verifier applies the same procedure to the checks**: before running a check it asks
   "does passing this check actually prove the goal?" (`meta_verifier.md`). A `CHECKS_INADEQUATE`
   verdict sends the definition of done back to be rewritten — recursion on the *criteria*, not just
   the work.
3. A failed decomposition re-enters the planner, which decomposes the *remainder* and recurses again.

### What stops infinite recursion

Four independent brakes — any one of them alone terminates the tree:

1. **Depth cap** `MAX_DEPTH = 3`. At the cap a node may not split; it must EXECUTE.
2. **Budget.** A node whose allocation is below `MIN_SPLIT_BUDGET = 8_000` may not split. A parent
   reserves 15% for its own verification before dividing the rest, so budget strictly decreases down
   the tree. `chat()` raises `BudgetExhausted` *before* a call it cannot afford.
3. **Attempt cap** `MAX_ATTEMPTS = 2` per node, and the inadequate-checks rewrite fires **at most
   once** per node (`test_checks_inadequate_rewrites_once_then_proceeds`).
4. **The monitor.** Identical action 3×, an A-B-A-B or A-B-C-A-B-C oscillation, or 8 steps with no
   file written and no movement in the check pass-count ends the attempt. A sub-goal that merely
   restates its parent is rejected at birth (`planner._is_restatement`).

Also: `MAX_TOOL_STEPS_PER_LEAF = 25`, and `MAX_CHILDREN = 5`.

### What makes "done" trustworthy

- **Independence is enforced, not requested.** `config.verifier_for()` maps every family to a
  different one; `solver.verify_and_decide` rejects a `DONE` whose verifier shares the worker's
  family and marks the node `failed`. Two tests cover it, including one that fails if the *config*
  ever allows a family to verify itself.
- **The verifier re-runs the checks itself.** It is handed the artifacts and a workspace listing —
  never the worker's transcript or its claims.
- **A model cannot talk past a failing check.** If it rules `DONE` while a check fails, the verdict
  is deterministically forced to `NOT_DONE` and the override is recorded in the trace.
- **Checks are judged before they are trusted.** More than one check that does not test the goal, or
  any aspect of the goal with no check at all, forces `CHECKS_INADEQUATE`.
- **Every check set contains a negative check.** "It printed something" is not done.
- **`needs_human` is rare and specific.** It fires for a provable impossibility (no credentials, no
  network, a self-contradiction) or two drift flags, and carries one answerable question.

## The two demo goals

```bash
uv run done run "@tasks/demo_01_cli_tool.txt"    # splits ~4 ways, lights up green from the leaves
uv run done run "@tasks/demo_02_impossible.txt"  # ends NEEDS_HUMAN, on purpose
```

`demo_02` asks for a script that deletes files in `/data` and **emails a report to the admin**.
There is no `/data` and there are no SMTP credentials in the sandbox. The honest outcome is not
"done" — it is the delete half completed behind a `--dry-run` guard (with a negative check proving
nothing is deleted without `--force`) and one question for a human: *which SMTP server and which
admin address?* An agent you can walk away from has to be able to do this.

## Crusoe, and why the price spread is the point

One OpenAI-compatible key, `https://api.inference.crusoecloud.com/v1`, gives us five-plus model
families across a ~20× price spread. That is what makes "a different family verifies every node"
affordable rather than theoretical: the expensive model plans at the root, **Nemotron Nano** does the
leaf work and every drift judgement, and verification is routed to whichever *other* family is
cheapest-capable. `done models` probes the live catalogue and writes the tier assignment and the
cross-family verifier map to `models.json`, so a model appearing or disappearing never needs a code
edit. If a model errors twice, `llm.py` falls back automatically and logs a `model_fallback` event —
the demo does not die on an outage.

Every run prints its own cost next to what the same token count would have cost at the root model's
rate.

## Built during the hackathon (Oct 17, 2026)

Every file in this repository is new work, written today. No pre-existing project, no agent
framework (no LangChain, no CrewAI) — the recursion, the budget algebra, the sandbox, the monitor
heuristics and the verifier protocol are all our own code in this repo.

```
done/config.py      done/llm.py        done/solver.py     done/planner.py
done/dod_writer.py  done/worker.py     done/verifier.py   done/monitor.py
done/checks.py      done/tools.py      done/sandbox.py    done/trace.py
done/context.py     done/models.py     done/report.py     done/cli.py
done/prompts/*.md   (12 prompts, loaded at runtime, never inlined)
viewer/index.html   (static tree viewer, vanilla JS)
tests/              (30 offline tests: recursion invariants, monitor, sandbox jail)
tasks/              (2 demo goals + 5 holdout goals)
```

Dependencies: `openai`, `pydantic`, `python-dotenv`, `rich`, `pytest`. That is the whole list.

- **Nemotron** runs every leaf and every drift judgement (`config.DEFAULT_TIERS[2]`,
  `drift_judge_model`), which is the cheap half of the price-spread argument above.
- `DECISIONS.md` is the append-only log of every judgment call made during the build.
