"""What every specialist shares: the request it receives, the run loop and its guards.

A specialist is a Pydantic AI agent whose output type is the outcome contract (minus
UNAVAILABLE, which only infrastructure produces). Around the model run sit three guards:

1. The kill switch, checked here at the callee even though the coordinator checks it too.
2. Grounding: an ANSWER may only cite records that a tool actually returned during this run.
   Citations the model made up are dropped, and an ANSWER left with none becomes DOC_GAP.
3. Any exception becomes UNAVAILABLE instead of an HTTP 500, so the coordinator always gets a
   parseable outcome.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol, cast

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ToolReturnPart

from agent_mesh.contracts import (
    Answer,
    Clarify,
    Decline,
    DocGap,
    Language,
    NotFound,
    Outcome,
    ReturnProposal,
    Unavailable,
    UnavailableReason,
)
from agent_mesh.control.kill_switch import KillSwitch
from agent_mesh.language import detect_language, localized

logger = logging.getLogger(__name__)

SpecialistOutput = Answer | Clarify | DocGap | NotFound | Decline
SPECIALIST_OUTPUT_TYPES: list[type[SpecialistOutput]] = [Answer, Clarify, DocGap, NotFound, Decline]
"""The kinds a model may return. UNAVAILABLE is left out: only infrastructure reports it."""

UNAVAILABLE_MESSAGE = {
    Language.EN: "This assistant is temporarily unavailable. Please try again later.",
    Language.FR: "Cet assistant est momentanément indisponible. Merci de réessayer plus tard.",
}
"""Same wording for every cause, so an outage never tells the user which control fired."""


class SpecialistRequest(BaseModel):
    """One call from the coordinator. `customer_id` comes from the verified request token."""

    text: str
    customer_id: str
    language: Language | None = None
    """Set by the coordinator's router; detected from `text` when a caller leaves it out."""
    confirmed_action: ReturnProposal | None = None
    """The exact action the customer approved; only the orders specialist acts on it."""
    confirmation: str | None = None
    """Coordinator-signed token bound to `confirmed_action` by hash."""


@dataclass(frozen=True)
class SpecialistDeps:
    """Run dependencies. The orders toolset copies `customer_id` into every MCP request `_meta`."""

    customer_id: str


class Specialist(Protocol):
    """Anything that turns a request into an outcome; the A2A server and in-process client
    depend on this, not on Pydantic AI."""

    name: str

    async def run(self, request: SpecialistRequest) -> Outcome:
        """Answer one request with exactly one outcome."""
        ...


def unavailable(agent: str, language: Language, reason: UnavailableReason) -> Unavailable:
    """The one way to build UNAVAILABLE, so the user-facing wording stays the same everywhere."""
    return Unavailable(
        language=language, message=UNAVAILABLE_MESSAGE[language], reason=reason, agent=agent
    )


def retrieved_record_ids(messages: list[ModelMessage]) -> set[str]:
    """Every SKU, order id and return id that a tool returned during the run."""
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in cast("dict[object, object]", value).items():
                if key in {"sku", "order_id", "return_id"} and isinstance(item, str):
                    found.add(item)
                walk(item)
        elif isinstance(value, list):
            for item in cast("list[object]", value):
                walk(item)
        elif isinstance(value, str) and value.lstrip().startswith(("{", "[")):
            try:
                walk(json.loads(value))
            except json.JSONDecodeError:
                return

    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolReturnPart):
                walk(part.content)
    return found


def ground_citations(outcome: SpecialistOutput, messages: list[ModelMessage]) -> SpecialistOutput:
    """Drop citations no tool returned. An ANSWER with nothing left to stand on is a DOC_GAP."""
    if not isinstance(outcome, Answer):
        return outcome
    known = retrieved_record_ids(messages)
    kept = [c for c in outcome.citations if c.doc_id in known]
    if kept:
        return outcome.model_copy(update={"citations": kept})
    logger.warning("answer dropped: no citation resolved to a retrieved record")
    subject = outcome.citations[0].doc_id if outcome.citations else "unknown"
    return DocGap(
        language=outcome.language,
        message=localized(
            outcome.language,
            "I could not verify that answer against our records.",
            "Je n'ai pas pu vérifier cette réponse dans nos données.",
        ),
        subject_id=subject,
    )


class PydanticAISpecialist:
    """Runs a Pydantic AI agent behind the guards described in the module docstring."""

    def __init__(
        self,
        name: str,
        agent: Agent[SpecialistDeps, SpecialistOutput],
        kill_switch: KillSwitch,
    ) -> None:
        self.name = name
        self._agent = agent
        self._kill_switch = kill_switch

    @asynccontextmanager
    async def mcp_servers_lifespan(self) -> AsyncGenerator[None]:
        """Keep the agent's MCP servers running for the life of the service, not per request."""
        async with self._agent:
            yield

    async def run(self, request: SpecialistRequest) -> Outcome:
        """Kill switch first, then the model run; never raises, so callers always get an outcome."""
        language = request.language or detect_language(request.text)
        if self._kill_switch.is_killed(self.name):
            return unavailable(self.name, language, UnavailableReason.KILL_SWITCH)
        try:
            return await self._run(request, language)
        except Exception:
            logger.exception("specialist %s failed", self.name)
            return unavailable(self.name, language, UnavailableReason.UPSTREAM_ERROR)

    async def _run(self, request: SpecialistRequest, language: Language) -> Outcome:
        deps = SpecialistDeps(customer_id=request.customer_id)
        prompt = f"[reply language: {language.value}]\n{request.text}"
        async with self._agent:
            result = await self._agent.run(prompt, deps=deps)
        return ground_citations(result.output, result.all_messages())
