# done — the Recursive Verifier Court

**Track: "Done Means Done" · Recursive Agents Hackathon · Oct 17, 2026 · built on Crusoe Managed Inference**

> An agent gets a one-line goal and a token budget. It either does the work and writes an
> **executable definition of done**, or it **splits the goal and calls itself** on each piece with a
> smaller budget. No node may report "done" until a verifier **from a model family that contributed
> nothing to the work** has re-run its checks from scratch. Nodes notice when they are looping or
> drifting, and choose: continue, re-plan, stop honestly, or — rarely — ask a human.

The failure this attacks: agents fill the holes in a vague goal with silent assumptions, then
confidently report success. Here the assumptions are printed, the checks are written *before* the
work starts, **the checks themselves are falsified before anyone trusts them**, and the agent that
did the work is never the agent that decides whether it is done.

```
          root · DeepSeek-V4-Pro ─── plans only, 1-2 calls, never touches a file
            │  declares the file layout as a binding contract
            │  SPLIT · budget 300k, 15% reserved, rest divided by share
    ┌───────┴────────┬────────────────┬────────────────┐
 storage.py       cli.py        tests/test_*.py     README.md
 [PREREQUISITE]   ↑ sees what storage.py actually became, not a description of it
 Nemotron-3-Super  Nemotron-3-Super  Nemotron-3-Super  Nemotron-3-Super
    │                │                │                │
 verified by      verified by      verified by      verified by
 gpt-oss-120b     gpt-oss-120b     gpt-oss-120b     gpt-oss-120b
 (openai ≠ nvidia, and openai contributed nothing to this node)
```

## Run it

```bash
cp .env.example .env            # put your CRUSOE_API_KEY in it
uv sync

uv run done models              # FIRST: what Crusoe actually serves → writes models.json
uv run done replay runs/demo-final/demo_02_refusal  # a finished run, instantly, zero LLM calls
uv run done index               # cache every run for the frontend
uv run done run "@tasks/demo_00_live.txt"    # a live run, ~90 seconds
uv run pytest -q                # 94 offline tests, ~15s
```

Frontend (static, no build step, two pages):

```bash
uv run done index               # cache every local run into runs/index.json
python3 -m http.server 8777     # from the repo root
open http://localhost:8777/viewer/      # gallery of every run
```

- **`viewer/index.html`** — the gallery. Every run as a card: verdict, how many checks passed,
  node count, how many model families touched it, tokens, cost, monitor flags, and any open
  human question. Filter by verdict; click through to a run.
- **`viewer/run.html?run=<id>`** — the trace for one run. The tree with live status colours,
  and a side panel with that node's goal, the assumptions it declared, every check with its
  `meta_verified` mark and output, the verifier's verdict and independent findings, monitor
  flags, and the report it gave its parent. Polls every 500 ms, so it follows a live run.

Both read `runs/index.json`, which `done run` refreshes automatically. Nothing scrapes a
directory listing, so the whole frontend works on any static host.

The cache committed here is built with `done index --preserved-only`: only the four runs under
`runs/demo-final/` ship with the repo, so every card in a fresh clone opens. Running anything
locally rewrites it to include your own runs — that one file showing as modified in `git status`
is expected.

## What makes a "done" claim trustworthy here

Six mechanisms, each of which exists because a live run broke without it. All are in
`DECISIONS.md` with the evidence.

**1. The checks are written before the work.** `dod_writer` runs before the worker touches
anything, so the definition of done cannot be retrofitted to whatever got built.

**2. The checks are falsified against an empty workspace.** Every non-negative check must *fail*
before any work exists. A check that already passes is a **tautology** and proves nothing. A check
that fails with a syntax or tooling error is **malformed** and could never pass — on the first live
run a node was failed for `import notes.cli if hasattr(...) else None`, which is not valid Python.
Both get rewritten before the worker starts. This step is deterministic and costs nothing unless
something is actually wrong.

**3. The verifier is independent, and independence is enforced against who *actually* worked.**
Not just "a different family than the configured model" — every model that produced work on a node
is recorded in `contributor_models`, and those families are disqualified from judging it. This
matters: when Crusoe rate-limited the Nemotron tier, work silently fell back to `gpt-oss-120b`,
and the old code still picked the verifier from the *configured* nvidia model — so openai verified
openai's own work while the trace claimed cross-family independence. That hole is closed in the
verifier choice, its fallback chain, and the final guard.

**4. The verifier never sees the worker's story.** It is handed the artifacts, a workspace listing
and read-only tools — not the transcript, not the worker's claims. It re-runs every check itself.

**5. A model cannot talk its way past a failing check.** A `DONE` verdict with any check failing is
deterministically forced to `NOT_DONE`, and the override is recorded in the trace.

**6. The checks are judged before they are trusted.** `meta_verifier` asks of each check: *would
passing this actually prove the goal?* If every check passes but the set is inadequate, the verdict
is `CHECKS_INADEQUATE` and the checks are rewritten — recursion applied to the criteria, not the
work. After one round of strengthening, a passing stricter set is accepted **with the verifier's
remaining doubts carried into the report as findings**, rather than arguing until the budget dies.

### Why it is actually recursive

1. `solve()` calls itself on sub-goals, with a smaller enforced budget each level down.
2. The verifier applies the same procedure to the checks (`meta_verifier.md`), and a
   `CHECKS_INADEQUATE` verdict sends the definition of done back to be rewritten.
3. A failed decomposition re-enters the planner, which decomposes only the remainder.

### What stops infinite recursion

Five independent brakes — any one alone terminates the tree:

1. **Depth cap** `MAX_DEPTH = 3`. At the cap a node must EXECUTE.
2. **Hierarchically enforced budget.** Every call is charged to the node *and its whole ancestor
   chain*, and refused when a node's subtree has spent its allocation. Before this was enforced,
   one branch consumed 211k of a 250k run against its own 63k allocation and its three siblings
   received literally zero tokens.
3. **Attempt cap** `MAX_ATTEMPTS = 2`, and the check rewrite fires at most once per node.
4. **The monitor.** Identical action 3×, an A-B-A-B or A-B-C-A-B-C oscillation, or 5 steps with no
   file written and no movement in the check pass-count. Genuine progress clears loop suspicion, so
   re-running checks after a fix is not mistaken for a loop.
5. **Deterministic anti-decomposition.** A goal naming exactly one file may not split — by regex,
   not by asking the model, which had already been asked nicely and declined.

### Coordination between parallel siblings

Siblings run concurrently in one shared workspace and cannot talk to each other. Two rules make
that safe, both added after watching it fail:

- **The root owns the layout.** Its first assumption must spell out every path. One run had
  `storage.py` written flat by one sibling while another built a `notes/` package and a third's
  tests could find neither.
- **A declared prerequisite runs first, alone**, and its siblings are handed a real directory
  listing of what it *actually* produced — not a description of what it was supposed to produce.

## The demo goals

```bash
uv run done run "@tasks/demo_00_live.txt"           # live, ~2 min, 2 artifacts
uv run done replay runs/demo-final/demo_01_tree     # the four-way notes CLI split, instant
uv run done replay runs/demo-final/demo_02_refusal  # the refusal, instant
uv run done run "@tasks/demo_02_impossible.txt"     # the refusal, live (~7 min)
```

`demo_02` asks for a script that deletes files in `/data` **and emails a report to the admin**.
There is no `/data` and there are no SMTP credentials. The honest outcome is not "done". On the
preserved run the delete script is built and verified (3/3), and the root carries two questions
no amount of further work could answer:

```
Human needed  YES — actual admin email address must be provided;
                    SMTP relay or mail server configuration must be available on the host
```

A node holding a question like that cannot reach `done` even with every check green — making the
unknown configurable and calling it finished is exactly the lie this project exists to prevent. On one run the verifier also caught something the checks had missed entirely — invalid
`find` syntax (`-delete -print` ordering) in a script whose own checks were passing. That is the
whole point: an agent you can walk away from has to be able to say "no".

## Honest performance

Measured, from `runs/`:

| | |
|---|---|
| Cost of a full four-way `demo_01` run | **~$0.03** (~300k tokens) |
| vs the same tokens priced at the root model | **7–10× cheaper** |
| Wall clock for a four-way split | **8–11 minutes** |
| Wall clock for `demo_00_live` | **~2 minutes** |
| Per leaf, clean pass | ~25–30k tokens |
| Per leaf, with one retry | ~40–55k tokens |

**The wall clock is the honest weak point.** Crusoe rate-limits per model (`429
max_requests_limit`), so concurrency is capped at 2 per model, and four siblings on one worker tier
serialise. A four-artifact goal is therefore a replay demo, not a live one. `demo_00_live` is sized
to finish inside a judging slot.

Reliability is also honest: `demo_01` reaches a fully green tree sometimes, and otherwise stops
with an exact account of which checks failed and why. **A stop is a correct outcome** — the system
is built to refuse, and the failures it reports have consistently been real.

## Crusoe, and why the price spread is the point

One OpenAI-compatible key at `https://api.inference.crusoecloud.com/v1` gives six model families
across a ~20× price spread. That is what makes "a different family verifies every node" affordable
rather than theoretical. `done models` probes the live catalogue and writes the tiers and the
cross-family verifier map to `models.json`, so a model appearing or disappearing never needs a code
edit — which mattered, because the catalogue differs from the docs: there is **no `meta-llama` on
Crusoe at all** (our configured fallback did not exist), the zai family is spelled `zai-org` and
ships `GLM-5.3`, and Nemotron carries an `NVIDIA-` prefix.

**Tier choice was measured, not assumed.** Nemotron Nano at the leaves burned 23–39k tokens per
node and landed checks at 1/3, 1/5, 0/2. Nemotron-3-Super did the same shape of work in 14k tokens
and 3 tool steps, clean. So:

- **`nvidia/NVIDIA-Nemotron-3-Super-120B-A12B` runs every node that does actual work** — every
  file written in a demo run is written by Nemotron.
- **`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` is the drift judge**, the job it is genuinely good at:
  scoring 0–10, called only when a cheap heuristic trips.
- `deepseek-ai/DeepSeek-V4-Pro` plans at the root and writes nothing.
- Verification routes to whichever family contributed nothing — usually `openai/gpt-oss-120b`.

If a model errors twice it gets a **cooldown, never a blacklist** (the old blacklist took the whole
worker tier out for a run after one burst of 429s), and every fallback is logged.

## Built during the hackathon (Oct 17, 2026)

Every file here is new work written today. No pre-existing project, no agent framework (no
LangChain, no CrewAI) — the recursion, budget algebra, sandbox, monitor heuristics, falsification
step and verifier protocol are all our own code.

```
done/config.py      done/llm.py        done/solver.py     done/planner.py
done/dod_writer.py  done/worker.py     done/verifier.py   done/monitor.py
done/checks.py      done/tools.py      done/sandbox.py    done/trace.py
done/context.py     done/models.py     done/report.py     done/cli.py
done/prompts/*.md   (13 prompts, loaded at runtime, never inlined)
done/index.py       (caches every run into runs/index.json for the frontend)
viewer/index.html   (gallery of every run)
viewer/run.html     (one run's tree + checks + verdicts, polls live)
tests/              (94 offline tests: recursion invariants, monitor, sandbox jail,
                     and a full end-to-end run with only the model calls faked)
tasks/              (3 demo goals + 5 holdout goals)
```

Dependencies: `openai`, `pydantic`, `python-dotenv`, `rich`, `pytest`. That is the whole list.

`runs/demo-final/` holds four real preserved runs (tree, full event log, and the artifacts the
agents produced) so every claim here can be checked with `done replay`, offline.

`DECISIONS.md` is the append-only log of every judgment call, including the twelve bugs that live
runs found and how each was fixed. Worth reading if you want to know what this cost to get right.
