"""Export golden cases and agent outputs for llm-regression-detector (dataset schema v2).

The only module that knows that format. Schema v2 as approved for the regression detector:

    {"schema_version": 2, "cases": [GoldenCase, ...]}
    GoldenCase: id, input_text (required); topic, expected_category, reference_answer,
    rubric (list of criteria), expected_summary_keywords, difficulty, notes,
    source (string map, not sent to models). Extra fields are rejected.

`expected_category` carries the outcome kind (ANSWER, CLARIFY, ...), checked by exact match.
For a two-step case (propose, then confirm) it is the kind expected after the confirmation,
because the prediction records the conversation's final outcome.
`source` names the market (FR, AE) instead of the demo customer id, because exported datasets
may be published and an id-shaped field invites copying real ids into them later.

`export_predictions` writes what the mesh actually answered, one JSON line per case keyed by
id, with the detector's output keys (`category`, `answer`). `lrd score --dataset --predictions`
judges that pair without re-running a prompt.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_mesh.domain.orders import CUSTOMERS
from agent_mesh.evals.cases import GoldenCase
from agent_mesh.evals.runner import CaseResult

SCHEMA_VERSION = 2
"""llm-regression-detector dataset schema this module writes."""
_MARKET_BY_CUSTOMER_ID = {c.customer_id: c.country.value for c in CUSTOMERS}


def to_regression_case(case: GoldenCase) -> dict[str, Any]:
    """One case in schema v2. Fields the case does not set are left out, not nulled."""
    exported: dict[str, Any] = {
        "id": case.id,
        "input_text": case.input_text,
        "topic": case.topic,
        "expected_category": (case.expected_after_confirm or case.expected).kind,
        "difficulty": case.difficulty,
        "source": {
            "origin": "agent-mesh",
            "agent": case.agent,
            "market": _MARKET_BY_CUSTOMER_ID[case.customer_id],
        },
    }
    if case.reference_answer is not None:
        exported["reference_answer"] = case.reference_answer
    if case.rubric:
        exported["rubric"] = case.rubric
    return exported


def export_dataset(cases: list[GoldenCase], path: Path) -> None:
    """Write a schema v2 dataset file."""
    payload = {"schema_version": SCHEMA_VERSION, "cases": [to_regression_case(c) for c in cases]}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def export_predictions(results: list[CaseResult], path: Path) -> None:
    """Write one predictions line per case, for `lrd score`."""
    lines = [
        json.dumps(
            {"id": r.case.id, "category": r.outcome.kind, "answer": r.outcome.message},
            ensure_ascii=False,
        )
        for r in results
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
