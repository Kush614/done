Your previous definition of done was REJECTED by an independent verifier as inadequate — it could
pass while the goal went unmet. Write a stricter set.

ORIGINAL GOAL: {{root_goal}}
THIS GOAL: {{goal}}

YOUR REJECTED CHECKS:
{{old_checks}}

THE VERIFIER'S COMPLAINT (take it literally):
{{verifier_reasoning}}

WORKSPACE RIGHT NOW:
{{workspace}}

Fix exactly what was complained about. Specifically:
- Any aspect of the goal with no check now gets one.
- Any check that could pass trivially (greps for a word, tests that something merely runs) becomes a
  check on actual behaviour and actual output values.
- Keep at least one negative check.
- Same format, same limits: 3-7 checks, no network, at most one llm_judge.

Return ONLY: {"checks": [...]}  (same schema as before)
