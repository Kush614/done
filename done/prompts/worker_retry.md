THIS IS ATTEMPT {{attempt}}. YOUR PREVIOUS ATTEMPT WAS REJECTED.

An independent verifier re-ran your checks and found:
{{failing}}

Its findings:
{{findings}}

Do not start over and do not re-explain. Change the specific things named above with
`write_file`, then call `run_checks` again, then `report_done`. If the verifier's complaint is that
a check is wrong rather than the code, fix the code to satisfy the check as written — the check is
the contract.

This is your last attempt. If a check still fails, call `report_done` with status="blocked" and name
the check. A false claim of done is caught and wastes the whole run.
