Before you run anything: judge the CHECKS themselves. A check that passes while the goal goes
unmet is how agents lie without meaning to.

ORIGINAL GOAL: {{root_goal}}
THE GOAL THIS NODE WAS GIVEN: {{goal}}

THE PROPOSED CHECKS:
{{checks}}

For EACH check answer: does passing this check actually provide evidence that the goal is met?
Say false when the check:
- asserts only that something ran, exists, or printed, with no claim about the result
- greps for a string that the agent could trivially write without implementing anything
- tests a detail irrelevant to the goal
- is a test file whose existence is checked but whose assertions are never run

Then answer separately: is any CRITICAL aspect of the goal covered by no check at all?

Return ONLY:
{"checks": [{"id": "c1", "meta_verified": true, "reason": "one line"}],
 "uncovered_aspects": ["..."],
 "verdict_hint": "ADEQUATE" | "INADEQUATE"}
