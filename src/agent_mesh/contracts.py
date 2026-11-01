"""Typed outcome contract shared by every agent in the mesh.

Every agent, specialist or coordinator, returns exactly one `Outcome`. The coordinator never
forwards free text from a specialist: it parses the specialist's A2A artifact into this union
and rejects anything else. That keeps a compromised or confused specialist from smuggling
instructions into the coordinator, and it gives evals a stable field to score.

Kinds and when each one applies:

- ANSWER: a grounded answer. Every claim comes from a cited catalog entry or order record.
- CLARIFY: the agent needs input from the user. It may carry a `pending_action` that only runs
  after the user confirms it (human in the loop).
- DOC_GAP: the subject exists but the data does not cover the question (for example a product
  attribute the catalog does not list).
- NOT_FOUND: the subject itself does not exist (unknown product or order).
- DECLINE: the agent refuses on purpose (out of scope, injection attempt, a policy rule).
  Another customer's order is NOT_FOUND, not DECLINE, so the reply does not confirm it exists.
- UNAVAILABLE: the agent could not run (kill switch, timeout, upstream error). Kept apart from
  DECLINE so that an outage is never scored as correct refusal behaviour.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


class Language(StrEnum):
    """Languages the assistant answers in."""

    EN = "en"
    FR = "fr"


class DeclineReason(StrEnum):
    """Why an agent declined. An enum, not free text, so evals and dashboards can group by it."""

    OUT_OF_SCOPE = "out_of_scope"
    PROMPT_INJECTION = "prompt_injection"
    POLICY = "policy"


class UnavailableReason(StrEnum):
    """Why an agent could not run."""

    KILL_SWITCH = "kill_switch"
    UPSTREAM_ERROR = "upstream_error"
    INVALID_RESPONSE = "invalid_response"


class Citation(BaseModel):
    """Points an answer to the record it came from.

    `doc_id` must resolve to a record a tool returned in the same run; the specialist drops
    citations it cannot resolve (see `specialists.base.ground_citations`).
    """

    model_config = ConfigDict(frozen=True)

    doc_id: str = Field(description="Catalog SKU, order id or return id the claim comes from.")
    source: Literal["catalog", "orders", "return_policy"]
    retrieved_on: date


class ReturnProposal(BaseModel):
    """A return request the orders agent wants to file. Runs only after the user confirms."""

    model_config = ConfigDict(frozen=True)

    action: Literal["create_return_request"] = "create_return_request"
    """Part of the hashed payload, so a confirmation can never be replayed for another action."""
    order_id: str
    sku: str
    quantity: int = Field(ge=1)
    reason: str = Field(max_length=200)


class _OutcomeBase(BaseModel):
    """Fields every outcome kind shares."""

    model_config = ConfigDict(frozen=True)

    language: Language
    message: str = Field(description="What the user reads, in `language`.")


class Answer(_OutcomeBase):
    """A grounded answer; at least one citation is required by the schema."""

    kind: Literal["ANSWER"] = "ANSWER"
    citations: list[Citation] = Field(min_length=1)


class Clarify(_OutcomeBase):
    """A question back to the user, optionally with a write waiting for a yes."""

    kind: Literal["CLARIFY"] = "CLARIFY"
    pending_action: ReturnProposal | None = None


class DocGap(_OutcomeBase):
    """The subject exists; the fact asked for is not documented."""

    kind: Literal["DOC_GAP"] = "DOC_GAP"
    subject_id: str = Field(description="The product or order that exists but lacks the data.")


class NotFound(_OutcomeBase):
    """No such product, or no such order on this customer's account."""

    kind: Literal["NOT_FOUND"] = "NOT_FOUND"


class Decline(_OutcomeBase):
    """A deliberate refusal with a groupable reason."""

    kind: Literal["DECLINE"] = "DECLINE"
    reason: DeclineReason


class Unavailable(_OutcomeBase):
    """An agent could not run; `agent` says which one."""

    kind: Literal["UNAVAILABLE"] = "UNAVAILABLE"
    reason: UnavailableReason
    agent: str


Outcome = Annotated[
    Answer | Clarify | DocGap | NotFound | Decline | Unavailable,
    Field(discriminator="kind"),
]
"""Discriminated union every agent returns. Parse with `OutcomeAdapter`."""

OutcomeAdapter: TypeAdapter[Outcome] = TypeAdapter(Outcome)
"""Validates outcome JSON from any agent into the right `Outcome` class."""

OutcomeKind = Literal["ANSWER", "CLARIFY", "DOC_GAP", "NOT_FOUND", "DECLINE", "UNAVAILABLE"]
"""Every `kind` value, for code that names a kind without building an outcome (eval cases)."""
