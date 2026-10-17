Is this agent still working on its goal?

ORIGINAL GOAL: {{root_goal}}
THIS AGENT'S GOAL: {{goal}}

ITS LAST FEW ACTIONS:
{{recent_steps}}

Score drift 0-10: 0 = squarely on the goal, 10 = doing something unrelated. Building scaffolding,
writing tests, and fixing failing checks are ON the goal, not drift. Rewriting unrelated files,
re-reading the same thing repeatedly, or chasing a different problem is drift.

Set "impossible": true ONLY if the goal cannot be satisfied in a sandbox with no network and no
credentials (it needs a real server, a real email account, a secret, or it contradicts itself).

Return ONLY: {"drift": 0, "reason": "one line", "impossible": false}
