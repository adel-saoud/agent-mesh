"""Run the golden suites and score them with deterministic checks.

This is the CI gate. It checks what code can check without a judge: outcome kind, language,
required citations, decline reason and whether a write was proposed. Answer quality (reference
answer, rubric) is scored by llm-regression-detector's judge on the exported dataset.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_mesh.contracts import Clarify, Decline, Outcome
from agent_mesh.evals.cases import ExpectedOutcome, GoldenCase
from agent_mesh.evals.harness import InProcessMesh, in_process_mesh
from agent_mesh.specialists.base import SpecialistRequest


@dataclass(frozen=True)
class CaseResult:
    """A case, what the mesh returned, and every expectation it missed."""

    case: GoldenCase
    outcome: Outcome
    failures: list[str] = field(default_factory=list[str])

    @property
    def passed(self) -> bool:
        """True when the outcome met every expectation."""
        return not self.failures


def check_outcome(outcome: Outcome, expected: ExpectedOutcome) -> list[str]:
    """Every mismatch, not just the first, so one run shows the whole regression."""
    failures: list[str] = []
    if outcome.kind != expected.kind:
        failures.append(f"kind {outcome.kind} != {expected.kind}")
    if expected.language is not None and outcome.language is not expected.language:
        failures.append(f"language {outcome.language} != {expected.language}")
    cited = {c.doc_id for c in getattr(outcome, "citations", [])}
    missing = set(expected.citations_include) - cited
    if missing:
        failures.append(f"missing citations {sorted(missing)}")
    if expected.decline_reason is not None and (
        not isinstance(outcome, Decline) or outcome.reason is not expected.decline_reason
    ):
        failures.append(f"decline reason != {expected.decline_reason}")
    if expected.has_pending_action is not None:
        has_action = isinstance(outcome, Clarify) and outcome.pending_action is not None
        if has_action is not expected.has_pending_action:
            failures.append(f"pending action {has_action} != {expected.has_pending_action}")
    return failures


async def run_case(mesh: InProcessMesh, case: GoldenCase) -> CaseResult:
    """Run one case against its agent; a coordinator case may answer a confirmation too."""
    request = SpecialistRequest(text=case.input_text, customer_id=case.customer_id)
    if case.agent == "catalog-agent":
        return CaseResult(
            case, outcome := await mesh.catalog.run(request), check_outcome(outcome, case.expected)
        )
    if case.agent == "orders-agent":
        return CaseResult(
            case, outcome := await mesh.orders.run(request), check_outcome(outcome, case.expected)
        )
    conversation = f"eval-{case.id}"
    outcome = await mesh.coordinator.ask(
        case.input_text, customer_id=case.customer_id, conversation_id=conversation
    )
    failures = check_outcome(outcome, case.expected)
    if case.confirm is not None and case.expected_after_confirm is not None and not failures:
        outcome = await mesh.coordinator.confirm(
            customer_id=case.customer_id, conversation_id=conversation, approved=case.confirm
        )
        failures = [
            f"after confirm: {f}" for f in check_outcome(outcome, case.expected_after_confirm)
        ]
    return CaseResult(case, outcome, failures)


async def run_suite(cases: list[GoldenCase], model: str) -> list[CaseResult]:
    """Each case gets a fresh mesh so a filed return never leaks into the next case."""
    results: list[CaseResult] = []
    for case in cases:
        async with in_process_mesh(model) as mesh:
            results.append(await run_case(mesh, case))
    return results
