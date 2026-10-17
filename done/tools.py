"""Tool schemas (OpenAI function-calling format) + the dispatcher the agents share."""

from __future__ import annotations

import json
from typing import Any

from .checks import run_checks
from .sandbox import SandboxViolation

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file from the workspace.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "path relative to the workspace root"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a text file in the workspace. Writes the WHOLE file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string", "description": "the complete new contents"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "Recursively list the workspace (or a subdirectory).",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "default": "."}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell",
            "description": "Run a shell command in the workspace. No network. 30s timeout.",
            "parameters": {
                "type": "object",
                "properties": {"cmd": {"type": "string"}},
                "required": ["cmd"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_checks",
            "description": "Run this node's definition-of-done checks and report which pass.",
            "parameters": {
                "type": "object",
                "properties": {
                    "check_ids": {
                        "type": "array", "items": {"type": "string"},
                        "description": "omit to run all checks",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "report_done",
            "description": "Finish. Only use status='done' when run_checks reports every check passing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["done", "blocked"]},
                    "summary": {"type": "string", "description": "what you built, or what blocked you"},
                    "needs_human": {
                        "type": "boolean",
                        "description": "true only if a human must supply information you cannot obtain",
                    },
                    "human_question": {"type": "string"},
                },
                "required": ["status", "summary"],
            },
        },
    },
]

# the verifier gets read-only tools plus run_checks: it inspects artifacts, it does not fix them
VERIFIER_TOOL_NAMES = {"read_file", "list_dir", "run_shell", "run_checks"}
VERIFIER_TOOL_SCHEMAS = [
    t for t in TOOL_SCHEMAS if t["function"]["name"] in VERIFIER_TOOL_NAMES
]


async def dispatch(name: str, args: dict, node: Any, ctx: Any, *, runner: str = "worker") -> str:
    """Execute one tool call and return a string the model can read."""
    sb = ctx.sandbox
    try:
        if name == "read_file":
            return sb.read_file(args.get("path", ""))
        if name == "write_file":
            if runner == "verifier":
                return "[refused] the verifier may not modify the workspace"
            return sb.write_file(args.get("path", ""), args.get("content", ""))
        if name == "list_dir":
            return sb.list_dir(args.get("path", ".") or ".")
        if name == "run_shell":
            res = await sb.run_shell(args.get("cmd", ""))
            return res.summary(limit=2500)
        if name == "run_checks":
            summary = await run_checks(
                node.dod, ctx, check_ids=args.get("check_ids") or None, runner=runner
            )
            return json.dumps(summary, indent=2)
        if name == "report_done":
            return json.dumps({"ack": True, **args})
        return f"[unknown tool: {name}]"
    except SandboxViolation as exc:
        return f"[sandbox refused] {exc}"
    except Exception as exc:  # noqa: BLE001 - tool errors are information, not crashes
        return f"[tool error] {type(exc).__name__}: {exc}"


def parse_args(raw: str | dict) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {"value": value}
    except json.JSONDecodeError:
        from .llm import parse_json_lenient
        return parse_json_lenient(raw or "")
