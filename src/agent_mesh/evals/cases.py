"""Golden eval cases for agent-mesh, one suite per agent.

The case format is a superset of llm-regression-detector's dataset schema v2: the deterministic
expectations (`expected`) are what the CI gate checks here, and `reference_answer` / `rubric` are
what the LLM judge scores once exported (see `export.py`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from agent_mesh.contracts import DeclineReason, Language, OutcomeKind

AgentUnderTest = Literal["coordinator", "catalog-agent", "orders-agent"]
"""The agent a case runs against; specialists are called directly, without the coordinator."""
Difficulty = Literal["easy", "medium", "hard", "adversarial"]
"""Same labels as llm-regression-detector, which reports scores per difficulty."""


class ExpectedOutcome(BaseModel):
    """What the outcome must satisfy. Unset fields are not checked."""

    model_config = ConfigDict(extra="forbid")

    kind: OutcomeKind
    language: Language | None = None
    citations_include: list[str] = Field(default_factory=list)
    decline_reason: DeclineReason | None = None
    has_pending_action: bool | None = None


class GoldenCase(BaseModel):
    """One golden case: who asks what, which agent answers, what the outcome must satisfy."""

    model_config = ConfigDict(extra="forbid")

    id: str
    agent: AgentUnderTest
    customer_id: str
    input_text: str
    topic: str
    expected: ExpectedOutcome
    confirm: bool | None = None
    """Coordinator only: answer the pending confirmation with this value."""
    expected_after_confirm: ExpectedOutcome | None = None
    difficulty: Difficulty = "easy"
    reference_answer: str | None = None
    rubric: list[str] | None = None


GoldenCaseListAdapter = TypeAdapter(list[GoldenCase])
"""Validates one suite file."""


def load_cases(path: Path) -> list[GoldenCase]:
    """Load a suite file; duplicate ids are an error because ids key baselines downstream."""
    cases = GoldenCaseListAdapter.validate_json(path.read_bytes())
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate case ids in {path}")
    return cases
