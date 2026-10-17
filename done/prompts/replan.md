A decomposition you made has partly failed. Route around the failure.

ORIGINAL GOAL: {{root_goal}}
YOUR GOAL: {{goal}}
ASSUMPTIONS ABOVE: {{ancestors_context}}

WHAT YOUR CHILDREN DID:
{{child_reports}}

WORKSPACE AS IT STANDS (the successful children's work is really there — build on it):
{{workspace}}

Produce a NEW, SMALLER decomposition that covers only what is still missing. Rules:
- Do not re-do work that succeeded. Name the surviving artifacts in the sub-goals that must use them.
- If a child failed because the goal needs something unobtainable (a credential, a network service),
  do NOT retry it — set "needs_human": true and give the one question a human must answer.
- 1 to 3 sub-goals. Smaller and more concrete than last time.

Return ONLY:
{"needs_human": false,
 "human_question": null,
 "reasoning": "...",
 "subgoals": [{"goal": "...", "why": "...", "budget_share": 1.0}]}
