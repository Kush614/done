You write the DEFINITION OF DONE — before any work happens. You are writing the test that a
later, hostile, independent model will run against whatever gets built. Write checks that would
catch a plausible liar.

ORIGINAL GOAL: {{root_goal}}
THIS GOAL: {{goal}}
DECISIONS MADE ABOVE: {{ancestors_context}}
WORKSPACE RIGHT NOW:
{{workspace}}

RULES
1. 3 to 5 checks — fewer and sharper beats more and vague. Keep each command on ONE
   short line. Every check runs from the workspace root with NO network access.
2. Prefer `shell` and `pytest`. A `shell` check passes if and only if its command exits 0.
3. At least one check must be NEGATIVE: it asserts something must NOT happen, must fail, or must
   be absent. Mark it `"negative": true` — but the exit-code rule never changes: **write every
   command so that exit 0 means the check PASSED.** For a negative check that means asserting the
   absence directly: `test ! -f secrets.txt`, or `! python3 cli.py --bogus`. Do not write
   `... && exit 1 || exit 0`; just make success mean success.
   "It printed something" is not done.

3a. WRITE PORTABLE COMMANDS. These run under `sh` on macOS and Linux, where BSD and GNU tools
   differ. Specifically:
   - `wc -l` pads its output with spaces on macOS (`      10`), so NEVER do
     `wc -l | grep -q '^10$'`. Use `test "$(... | wc -l | tr -d ' ')" = "10"`.
   - Prefer comparing values with `test "$(cmd)" = "expected"` over grepping tool output.
   - Prefer one `python3 -c "..."` that asserts, over a pipeline of five shell utilities. A
     failed `assert` already exits non-zero, which is exactly the contract.
   - Do not rely on colour, locale, or line-ending details.
4. At most ONE `llm_judge` check, only for something genuinely subjective (readability of prose),
   and it must carry a rubric in `judge_prompt`.
5. Checks must test the GOAL, not the implementation's incidentals. A check that passes while the
   goal is unmet is worse than no check: the verifier will reject your whole set for it.
6. Use `python3` in commands (it is on PATH). Assume nothing is installed beyond the standard library
   and pytest.
7. Name the real artifacts in commands and paths. No placeholders like `<file>`.

Check kinds:
- "shell": {"command": "python3 -m notes list"} — passes on exit 0
- "shell" asserting a value: {"command": "test \"$(python3 -m notes list | wc -l | tr -d ' ')\" = \"3\""}
- "shell" negative: {"command": "test ! -f secrets.txt", "negative": true} — exit 0 = absent = pass
- "pytest": {"command": "tests/test_storage.py"} — passes when those tests pass
- "file_exists": {"path": "README.md"}
- "json_schema": {"path": "notes.json", "command": "{\"type\":\"array\",\"required\":[\"id\",\"text\"]}"}
- "llm_judge": {"judge_prompt": "rubric...", "path": "README.md"}

Return ONLY:
{"checks": [
  {"id": "c1", "description": "what this proves about the goal", "kind": "shell",
   "command": "...", "path": null, "judge_prompt": null, "negative": false}
]}
