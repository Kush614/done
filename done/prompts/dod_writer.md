You write the DEFINITION OF DONE — before any work happens. You are writing the test that a
later, hostile, independent model will run against whatever gets built. Write checks that would
catch a plausible liar.

ORIGINAL GOAL: {{root_goal}}
THIS GOAL: {{goal}}
DECISIONS MADE ABOVE: {{ancestors_context}}
WORKSPACE RIGHT NOW:
{{workspace}}

RULES
1. 3 to 7 checks. Every check must run from the workspace root with NO network access.
2. Prefer `shell` and `pytest`. A `shell` check passes if and only if its command exits 0.
3. At least one check must be NEGATIVE: something that must fail, error, or be absent. Set
   `"negative": true` on it and write the command so that the command SUCCEEDING means the bad
   thing happened. Example: `test -f secrets.txt` with negative=true passes when the file is absent.
   "It printed something" is not done.
4. At most ONE `llm_judge` check, only for something genuinely subjective (readability of prose),
   and it must carry a rubric in `judge_prompt`.
5. Checks must test the GOAL, not the implementation's incidentals. A check that passes while the
   goal is unmet is worse than no check: the verifier will reject your whole set for it.
6. Use `python3` in commands (it is on PATH). Assume nothing is installed beyond the standard library
   and pytest.
7. Name the real artifacts in commands and paths. No placeholders like `<file>`.

Check kinds:
- "shell": {"command": "python3 -m notes list"} — passes on exit 0
- "pytest": {"command": "tests/test_storage.py"} — passes when those tests pass
- "file_exists": {"path": "README.md"}
- "json_schema": {"path": "notes.json", "command": "{\"type\":\"array\",\"required\":[\"id\",\"text\"]}"}
- "llm_judge": {"judge_prompt": "rubric...", "path": "README.md"}

Return ONLY:
{"checks": [
  {"id": "c1", "description": "what this proves about the goal", "kind": "shell",
   "command": "...", "path": null, "judge_prompt": null, "negative": false}
]}
