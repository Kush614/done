"""Shared pydantic data model (SPEC §3.1). Kept in its own module to avoid import cycles."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CheckKind = Literal["shell", "pytest", "file_exists", "json_schema", "llm_judge"]
NodeStatus = Literal[
    "pending", "planning", "executing", "verifying", "replanning",
    "done", "failed", "stopped", "needs_human",
]
Verdict = Literal["DONE", "NOT_DONE", "CHECKS_INADEQUATE"]


class Check(BaseModel):
    id: str
    description: str
    kind: CheckKind = "shell"
    command: str | None = None
    path: str | None = None
    judge_prompt: str | None = None
    negative: bool = False          # True if this check asserts something should NOT happen
    meta_verified: bool = False     # verifier confirmed this check actually tests the goal
    meta_reason: str | None = None
    passed: bool | None = None
    output: str | None = None


class Verification(BaseModel):
    verifier_model: str
    checks_passed: int = 0
    checks_total: int = 0
    independent_findings: list[str] = Field(default_factory=list)
    verdict: Verdict = "NOT_DONE"
    reasoning: str = ""


class MonitorFlag(BaseModel):
    kind: Literal["LOOP", "DRIFT", "BUDGET"]
    evidence: str
    step: int


class Node(BaseModel):
    id: str
    parent_id: str | None = None
    depth: int = 0
    goal: str
    root_goal: str
    ancestors_context: str = ""
    budget_tokens: int = 0
    model: str = ""
    status: NodeStatus = "pending"
    decision: Literal["EXECUTE", "SPLIT"] | None = None
    is_prerequisite: bool = False   # runs before its siblings; they see its real artifacts
    children: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    human_questions: list[str] = Field(default_factory=list)
    dod: list[Check] = Field(default_factory=list)
    verification: Verification | None = None
    verification_history: list[Verification] = Field(default_factory=list)
    attempts: int = 0
    dod_rewrites: int = 0
    meta_signature: str | None = None       # which check set the meta verdicts belong to
    meta_cache: dict | None = None
    contributor_models: list[str] = Field(default_factory=list)  # who actually did the work
    tool_steps: int = 0
    tokens_used: int = 0
    cost_usd: float = 0.0
    subtree_tokens: int = 0        # this node plus everything below it
    subtree_cost_usd: float = 0.0
    flags: list[MonitorFlag] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)
    final_report: str | None = None

    def log(self, line: str) -> None:
        self.events.append(line)

    @property
    def terminal(self) -> bool:
        return self.status in ("done", "failed", "stopped", "needs_human")
