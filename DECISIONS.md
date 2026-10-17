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
