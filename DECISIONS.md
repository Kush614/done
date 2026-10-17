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
