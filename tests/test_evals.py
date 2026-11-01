"""Golden suites, deterministic checks and the export to the regression detector's schema v2."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from agent_mesh.contracts import (
    Answer,
    Citation,
    Clarify,
    Decline,
    DeclineReason,
    Language,
    NotFound,
    ReturnProposal,
)
from agent_mesh.evals.cases import ExpectedOutcome, GoldenCase, load_cases
from agent_mesh.evals.export import export_dataset, export_predictions, to_regression_case
from agent_mesh.evals.runner import CaseResult, check_outcome, run_suite

GOLDEN_DIR = Path(__file__).parent.parent / "evals" / "golden"
SCHEMA_V2_FIELDS = {
    "id",
    "input_text",
    "topic",
    "expected_category",
    "reference_answer",
    "rubric",
    "expected_summary_keywords",
    "difficulty",
    "notes",
    "source",
}


def _case(**overrides: object) -> GoldenCase:
    fields: dict[str, object] = {
        "id": "c1",
        "agent": "catalog-agent",
        "customer_id": "cus-fr-001",
        "input_text": "price of KW-KET-002",
        "topic": "product_price",
        "expected": {"kind": "ANSWER"},
    }
    fields.update(overrides)
    return GoldenCase.model_validate(fields)


@pytest.mark.parametrize("path", sorted(GOLDEN_DIR.glob("*.json")), ids=lambda p: p.stem)
async def test_golden_suite_passes_offline(path: Path) -> None:
    results = await run_suite(load_cases(path), "offline")
    assert [(r.case.id, r.failures) for r in results if not r.passed] == []


def test_every_agent_has_a_bilingual_suite() -> None:
    cases = [c for path in GOLDEN_DIR.glob("*.json") for c in load_cases(path)]
    assert {c.agent for c in cases} == {"coordinator", "catalog-agent", "orders-agent"}
    assert any(c.expected.language is Language.FR for c in cases)
    assert any(c.difficulty == "adversarial" for c in cases)


def test_duplicate_ids_rejected(tmp_path: Path) -> None:
    path = tmp_path / "dup.json"
    case = _case().model_dump(mode="json")
    path.write_text(json.dumps([case, case]))
    with pytest.raises(ValueError, match="duplicate"):
        load_cases(path)


def test_check_reports_each_mismatch() -> None:
    answer = Answer(
        language=Language.EN,
        message="m",
        citations=[Citation(doc_id="A", source="catalog", retrieved_on=date(2026, 10, 20))],
    )
    failures = check_outcome(
        answer,
        ExpectedOutcome(
            kind="DECLINE",
            language=Language.FR,
            citations_include=["B"],
            decline_reason=DeclineReason.POLICY,
            has_pending_action=True,
        ),
    )
    assert len(failures) == 5
    assert check_outcome(answer, ExpectedOutcome(kind="ANSWER", citations_include=["A"])) == []
    decline = Decline(language=Language.EN, message="m", reason=DeclineReason.OUT_OF_SCOPE)
    assert (
        check_outcome(decline, ExpectedOutcome(kind="DECLINE", decline_reason=DeclineReason.POLICY))
        != []
    )
    proposal = ReturnProposal(order_id="ORD-1", sku="KW-KET-002", quantity=1, reason="r")
    clarify = Clarify(language=Language.EN, message="m", pending_action=proposal)
    assert check_outcome(clarify, ExpectedOutcome(kind="CLARIFY", has_pending_action=True)) == []


def test_export_matches_schema_v2(tmp_path: Path) -> None:
    case = _case(reference_answer="About 30 EUR.", rubric=["Gives the price"])
    exported = to_regression_case(case)
    assert set(exported) <= SCHEMA_V2_FIELDS
    assert exported["expected_category"] == "ANSWER"
    assert exported["source"] == {"origin": "agent-mesh", "agent": "catalog-agent", "market": "FR"}
    assert "cus-" not in json.dumps(exported)
    assert "reference_answer" not in to_regression_case(_case())

    dataset = tmp_path / "dataset.json"
    export_dataset([case], dataset)
    payload = json.loads(dataset.read_text())
    assert payload["schema_version"] == 2
    assert payload["cases"][0]["id"] == "c1"


def test_two_step_case_exports_the_final_kind() -> None:
    case = _case(
        agent="coordinator",
        expected={"kind": "CLARIFY", "has_pending_action": True},
        confirm=True,
        expected_after_confirm={"kind": "ANSWER"},
    )
    assert to_regression_case(case)["expected_category"] == "ANSWER"


def test_export_predictions(tmp_path: Path) -> None:
    outcome = NotFound(language=Language.EN, message="nothing")
    path = tmp_path / "predictions.jsonl"
    export_predictions([CaseResult(_case(), outcome)], path)
    line = json.loads(path.read_text().splitlines()[0])
    assert line == {"id": "c1", "category": "NOT_FOUND", "answer": "nothing"}
