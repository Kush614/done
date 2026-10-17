# DECISIONS.md — append-only log of judgment calls

Format: `HH:MM — decision — why`

## Oct 17, 2026

- **12:15 — Pydantic models live in `done/models.py`, not `solver.py`.** SPEC §3.1 shows them inside
  `solver.py`, but `planner`, `worker`, `verifier` and `monitor` all need `Node`/`Check`, and importing
  them from `solver` creates an import cycle (`solver` imports `planner` imports `solver`). One shared
  leaf module, zero cycles. Everything else matches the spec layout.
- **12:15 — `dod_writer` is its own module (`done/dod_writer.py`).** SPEC §1's file list omits it but
  §3.2/§4.2 call `dod_writer.write/rewrite/integration_checks`. It owns three prompts; folding it into
  `worker.py` would hide the fact that the DoD is written *before* the worker runs.
- **12:15 — Live model IDs land in `models.json` at the repo root, read by `config.py`.** `done models`
  writes it. Keeps "model IDs in ONE place" true while letting us re-probe Crusoe without a code edit.
  Hardcoded SPEC defaults remain as the fallback when `models.json` is absent.
- **12:16 — Crusoe base URL confirmed reachable:** unauthenticated `GET /v1/models` returns
  `401 {"errors":["Authentication failed"]}`, so the host and path from SPEC §2 are right.
- **12:55 — `worker_retry.md` now names `run_checks`/`report_done` explicitly.** A test asserted every tool-loop prompt names the tool it must call; the retry prompt said "re-run the checks" in prose, which is exactly the vagueness this project is about. Strengthened the prompt instead of loosening the test, and added the last-attempt instruction to report blocked rather than claim done.
- **13:05 — The sandbox puts our own `bin/` first on `PATH`; checks are never text-rewritten.**
  The first version substituted `python3` → `sys.executable` inside check commands, which
  re-substituted into its own output (`.../bin/` + `.../bin/python3`) and broke every `pytest`
  check. Caught by the end-to-end fake-LLM test, which is why that test exists. Now a check can
  write plain `python3 -m pytest` and get an interpreter that has pytest.
- **13:05 — Added `tests/test_end_to_end_fake_llm.py`: the real worker loop, real tools, real
  sandbox, real check runner and the real verifier gate, with only `llm.chat` faked.** It covers
  the vertical slice end to end offline — including a worker that claims done without running its
  checks (the verifier runs them anyway) and a deliberately useless check set (sent back to be
  rewritten exactly once).
- **14:00 — `done models` against the live catalogue: 13 models, 6 families.** The spec's IDs were
  close but wrong in three ways that would have killed the demo: **no `meta-llama` on Crusoe at
  all** (the configured fallback model did not exist), the zai family is spelled `zai-org` and ships
  `GLM-5.3` not `GLM-5.1`, and Nemotron carries an `NVIDIA-` prefix
  (`nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`). Live IDs now in `config.py` defaults *and* `models.json`.
  Full catalogue: Qwen/Qwen3.8-27B · deepseek-ai/{DeepSeek-V4-Pro, DeepSeek-V4.1-Flash,
  Deepseek-V4-Flash} · google/gemma-4-31b-it · nvidia/{NVIDIA-Nemotron-3-Nano-30B-A3B,
  NVIDIA-Nemotron-3-Super-120B-A12B, NVIDIA-Nemotron-3.5-Super-VL,
  Nemotron-3-Nano-Omni-Reasoning-30B-A3B, Nemotron-3.5-Lightning-30B-A3B} · openai/gpt-oss-120b ·
  zai-org/{GLM-5.3, GLM-5.3-Flash}.
- **14:05 — `_pick_tiers` keeps the hand-picked verifier pairs when they are live, and only
  synthesizes for families it has no pairing for.** Pure rotation handed every Nemotron leaf to
  `Qwen/Qwen3.8-27B` — an unknown quantity for JSON compliance — when `gpt-oss-120b` was right
  there. Adaptivity should fill gaps, not override judgement.
- **14:10 — Verification independence is judged on the model that ANSWERED, not the one we asked
  for.** A mid-call fallback could hand the worker's own family the final say while the trace still
  claimed cross-family verification. `chat()` now takes an explicit fallback chain, the verifier
  passes a cross-family-only one, and a same-family ruling is forced to NOT_DONE and traced as
  `independence_lost`.
- **14:10 — One exit-code rule for every command check: exit 0 means PASS, negative checks
  included.** The runner used to invert `negative` checks, which double-negated the self-inverting
  commands models actually write (`... && exit 1 || exit 0`) and failed *correct* code in the first
  live run. `negative` is now descriptive metadata; `file_exists` is the one kind that still
  inverts, because it has no command to express an absence in.
- **14:10 — The check writer is taught BSD vs GNU.** The first live run's `hello.py` was correct and
  was failed by `wc -l | grep -q '^10$'`: macOS `wc` pads with leading spaces. The prompt now
  mandates `test "$(... | wc -l | tr -d ' ')" = "N"` and prefers one asserting `python3 -c` over a
  pipeline of shell utilities.
- **14:15 — Budget is carved per node, not just globally.** The first run let the worker spend 21k of
  25k, leaving too little for the verifier's second call, so adjudication was silently skipped and
  the verdict rested on check results alone. Now a worker may spend at most `WORKER_SHARE = 0.60` of
  its own node's allocation, and the global reserve has an absolute floor (`MIN_VERIFY_RESERVE`)
  covering both verification calls. Verification cost scales with node count, so a single global
  percentage was the wrong shape.
- **14:15 — Real progress clears loop suspicion.** The monitor flagged LOOP on the exact
  `run_checks` call that reached 7/7, because it was the third identical call. An improvement in the
  pass count now resets the fingerprint history: re-running checks after a fix is the behaviour we
  ask for. A stuck check loop (same call, same result, 3x) is still caught.
- **14:25 — `_fallback_checks` must never replace *good* checks.** The first live split run
  truncated three check-writer responses at `max_tokens=1800`, and the generic fallback set
  (`f1/f2/f3`) silently took their place — the meta-verifier then correctly rejected those as not
  testing the goal, and the node died arguing with itself. Now: `max_tokens=3200`, the prompt asks
  for 3-5 terse checks, and on an unparseable rewrite the EXISTING checks are kept. The generic set
  is only for a node that never had any.
- **14:45 — Inadequate checks only matter when they would otherwise produce a DONE.** A failing
  check already settles the verdict (NOT_DONE, fix the code). Forcing `CHECKS_INADEQUATE` while
  checks were failing sent the worker round again over a complaint about the *checks* — the single
  largest budget sink measured (one node spent 26k re-doing work nobody had faulted). The gate now
  scrutinises the check set only when every check passes.
- **14:45 — A check rewrite re-verifies; it does not redo the work.** Same reason: the artifacts
  were never the complaint. And after one round of strengthening, a passing stricter set is
  accepted with the verifier's remaining doubts carried into the report as findings, rather than
  looping until the budget dies.
- **14:45 — The meta-verifier is told to be strict about the word CRITICAL.** It was forcing full
  rewrites over "usage examples for list/search/delete" and "instructions on how to run the tests".
  One pedantic entry cost a whole rewrite-and-reverify cycle.
- **15:00 — Token attribution moved to a ContextVar.** `ctx._current_node_id` was a single
  attribute, and siblings run concurrently under `asyncio.gather`, so every call was charged to
  whichever sibling happened to run last (root.1 reported 13.7k against an actual 46k). A
  ContextVar is copied per task. This was not cosmetic: the worker's per-node ceiling and the
  monitor's BUDGET flag are computed from these numbers. `llm.charge()` is now the single charging
  path, shared by the real client and the test double so attribution is genuinely under test.
- **15:00 — Subtree totals are recomputed on every tree write.** A rollup taken when the children
  finished went stale as soon as the parent paid for its own integration checks and verification.
