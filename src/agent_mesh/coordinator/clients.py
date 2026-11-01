"""How the coordinator reaches a specialist: in process, or over A2A.

Both sides of `SpecialistClient` return the same `Outcome`, so the coordinator graph does not
know or care which one it uses. Evals and unit tests run in process (fast, deterministic); the
deployed mesh runs over A2A because each specialist is a separate service with its own owner,
release cadence and trust zone (see docs/architecture.md, "When to split an agent out").

The A2A client fails closed: it verifies the specialist's signed card before the first call,
and anything that is not a valid outcome comes back as UNAVAILABLE, never as raw text.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

import httpx
from a2a.client import (
    A2ACardResolver,
    A2AClientError,
    AuthInterceptor,
    ClientCallContext,
    ClientConfig,
    ClientFactory,
)
from a2a.helpers import get_data_parts, new_message, new_text_part
from a2a.types import AgentCard, Message, Role, SendMessageRequest
from pydantic import ValidationError

from agent_mesh.contracts import Outcome, OutcomeAdapter, UnavailableReason
from agent_mesh.language import detect_language
from agent_mesh.observability.tracing import trace_headers
from agent_mesh.security.auth import MeshTokenCredentials, client_call_context
from agent_mesh.security.cards import verify_card
from agent_mesh.security.keys import SigningKey, TrustStore
from agent_mesh.security.tokens import TokenError
from agent_mesh.specialists.base import Specialist, SpecialistRequest, unavailable

logger = logging.getLogger(__name__)


class SpecialistClient(Protocol):
    """Reaches a specialist by name and always returns an outcome, never raises."""

    async def call(self, agent: str, request: SpecialistRequest) -> Outcome:
        """Run `request` on the specialist named `agent`."""
        ...


class InProcessSpecialistClient:
    """Calls specialist objects directly: evals and tests, same contract, no network."""

    def __init__(self, specialists: dict[str, Specialist]) -> None:
        self._specialists = specialists

    async def call(self, agent: str, request: SpecialistRequest) -> Outcome:
        """Run the specialist object directly; it applies the same guards as over A2A."""
        return await self._specialists[agent].run(request)


def _request_metadata(request: SpecialistRequest) -> dict[str, Any]:
    """A2A message metadata for the fields that are not the question text."""
    metadata: dict[str, Any] = {}
    if request.language is not None:
        metadata["language"] = request.language.value
    if request.confirmed_action is not None:
        metadata["confirmed_action"] = request.confirmed_action.model_dump(mode="json")
        metadata["confirmation"] = request.confirmation or ""
    return metadata


class A2ASpecialistClient:
    """Calls specialists over A2A with a verified card and a fresh token per call."""

    def __init__(
        self,
        *,
        token_key: SigningKey,
        trust: TrustStore,
        agent_urls: dict[str, str],
        token_ttl_seconds: int,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        """`agent_urls` maps agent name to URL, also each token's audience. `transport` lets
        tests route calls to in-process ASGI apps."""
        self._credentials = MeshTokenCredentials(token_key, ttl_seconds=token_ttl_seconds)
        self._trust = trust
        self._agent_urls = agent_urls
        self._transport = transport
        self._timeout_seconds = timeout_seconds
        self._verified_cards: dict[str, AgentCard] = {}

    def _http_client(self, headers: dict[str, str] | None = None) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers=headers, transport=self._transport, timeout=self._timeout_seconds
        )

    async def _verified_card(self, agent: str) -> AgentCard:
        """Fetch and verify the card once. A card that fails verification is never cached."""
        if agent not in self._verified_cards:
            async with self._http_client() as http:
                card = await A2ACardResolver(http, self._agent_urls[agent]).get_agent_card()
            verify_card(card, expected_principal=agent, trust=self._trust)
            self._verified_cards[agent] = card
        return self._verified_cards[agent]

    async def call(self, agent: str, request: SpecialistRequest) -> Outcome:
        """Send over A2A; any transport, card or parsing failure becomes UNAVAILABLE."""
        language = request.language or detect_language(request.text)
        try:
            outcome = await self._send(agent, request)
        except TokenError:
            logger.exception("refusing %s: card verification failed", agent)
            return unavailable(agent, language, UnavailableReason.UPSTREAM_ERROR)
        except (httpx.HTTPError, A2AClientError, ValidationError, KeyError, ValueError):
            logger.exception("call to %s failed", agent)
            return unavailable(agent, language, UnavailableReason.UPSTREAM_ERROR)
        if outcome is None:
            return unavailable(agent, language, UnavailableReason.INVALID_RESPONSE)
        return outcome

    async def _send(self, agent: str, request: SpecialistRequest) -> Outcome | None:
        card = await self._verified_card(agent)
        message = new_message([new_text_part(request.text)], role=Role.ROLE_USER)
        message.metadata.update(_request_metadata(request))
        context = client_call_context(audience=self._agent_urls[agent], subject=request.customer_id)
        async with self._http_client(trace_headers()) as http:
            return await send_for_outcome(http, card, message, self._credentials, context)


async def send_for_outcome(
    http: httpx.AsyncClient,
    card: AgentCard,
    message: Message,
    credentials: MeshTokenCredentials,
    context: ClientCallContext,
) -> Outcome | None:
    """Send one message and parse the `outcome` data artifact. None if the reply has none.

    The bearer token is added by a2a-sdk's `AuthInterceptor` for the scheme the card declares.
    """
    client = ClientFactory(ClientConfig(httpx_client=http, streaming=False)).create(
        card, interceptors=[AuthInterceptor(credentials)]
    )
    request = SendMessageRequest(message=message)
    async for event in client.send_message(request, context=context):
        for artifact in event.task.artifacts:
            for data in get_data_parts(artifact.parts):
                return OutcomeAdapter.validate_python(data)
    return None
