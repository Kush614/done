You are deciding one thing about a sub-goal your parent handed you: do it yourself, or break it up?

ORIGINAL GOAL: {{root_goal}}
YOUR GOAL: {{goal}}
WHAT WAS DECIDED ABOVE YOU (binding — especially the file layout):
{{ancestors_context}}

Count the deliverable artifacts in YOUR goal. An artifact is a file, or a tightly-coupled group of
files, that someone could review on its own.
- ONE artifact → "EXECUTE". This is the usual answer: your parent already did the decomposition.
- TWO OR MORE genuinely independent artifacts → "SPLIT" into 2-{{max_children}} sub-goals, each
  naming its own files and honouring the layout above.

State any assumption you are making that your parent did not already fix for you. If something you
need can only come from a human (a credential, a server address, a decision), start that line with
"NEEDS HUMAN: ".

Return ONLY:
{"decision": "EXECUTE" | "SPLIT",
 "reasoning": "one or two sentences",
 "assumptions": ["..."],
 "subgoals": [{"goal": "...", "why": "...", "budget_share": 0.5, "prerequisite": false}],
 "leaf_signal": "single_tool_call" | "small_file" | "needs_decomposition"}
