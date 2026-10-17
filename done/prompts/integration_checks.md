Your sub-agents each finished and verified their own piece. You now write the checks that only make
sense once all the pieces exist together — the ones that catch the parts not fitting.

ORIGINAL GOAL: {{root_goal}}
YOUR GOAL: {{goal}}
ASSUMPTIONS MADE ABOVE: {{ancestors_context}}

WHAT YOUR CHILDREN REPORT THEY BUILT:
{{child_reports}}

WORKSPACE RIGHT NOW:
{{workspace}}

Write 3 to 6 INTEGRATION checks. These must exercise the seams:
- the whole thing invoked end to end, the way a user would invoke it
- every piece a child claimed, reachable from that entry point
- data written by one piece and read back by another
- the full test suite, together, in one command
- at least one negative check (a bad invocation must fail, a destructive path must refuse)

Do NOT re-test what a single child already verified in isolation. Test that they compose.
Commands run from the workspace root with no network. Use `python3`.

Return ONLY: {"checks": [...]}  (same schema as the definition-of-done writer)
