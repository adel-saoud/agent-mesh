"""Serve any agent of the mesh over A2A with a2a-sdk.

One code path for every agent in the mesh, including the coordinator. a2a-sdk is used on both
sides of every hop: fasta2a 2.1.1 and a2a-sdk 1.2.3 do not interoperate today (fasta2a writes
task timestamps without a timezone, which a2a-sdk's protobuf parser rejects).

Wire format of one call:
- request: one text part with the user's question, plus message metadata:
  `language`, and for a confirmed write `confirmed_action` + `confirmation`.
- response: a completed task with one data artifact named `outcome` holding the outcome JSON.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from a2a.helpers import new_data_part, new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCard
from a2a.utils.constants import DEFAULT_RPC_URL
from google.protobuf.json_format import MessageToDict
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from agent_mesh.config import HEALTH_PATH
from agent_mesh.contracts import Language, Outcome, ReturnProposal
from agent_mesh.observability.tracing import TraceContextMiddleware
from agent_mesh.security.auth import MeshTokenBackend, unauthorized_response, verified_claims
from agent_mesh.security.keys import TrustStore
from agent_mesh.specialists.base import Specialist, SpecialistRequest

OUTCOME_ARTIFACT_NAME = "outcome"
"""Name of the one data artifact every reply carries; callers parse it into an `Outcome`."""


def request_from_context(context: RequestContext) -> SpecialistRequest:
    """Build the specialist request. The customer is the token's `sub`, nothing else."""
    claims = verified_claims(context.call_context)
    metadata: dict[str, Any] = (
        MessageToDict(context.message.metadata) if context.message is not None else {}
    )
    action = metadata.get("confirmed_action")
    language = metadata.get("language")
    return SpecialistRequest(
        text=context.get_user_input(),
        customer_id=str(claims["sub"]),
        language=Language(language) if language else None,
        confirmed_action=ReturnProposal.model_validate(action) if action else None,
        confirmation=metadata.get("confirmation"),
    )


OutcomeHandler = Callable[[RequestContext], Awaitable[Outcome]]
"""Turns one A2A request into one outcome. Specialists and the coordinator each provide one."""


def specialist_handler(specialist: Specialist) -> OutcomeHandler:
    """Adapt a specialist to the A2A executor."""

    async def handle(context: RequestContext) -> Outcome:
        return await specialist.run(request_from_context(context))

    return handle


class OutcomeExecutor(AgentExecutor):
    """Answers every request with one completed task holding one `outcome` data artifact."""

    def __init__(self, handler: OutcomeHandler) -> None:
        self._handler = handler

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Run the handler and publish its outcome; there is no streaming or partial result."""
        if context.message is None:
            raise ValueError("A2A request without a message")
        task = context.current_task or new_task_from_user_message(context.message)
        await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        outcome = await self._handler(context)
        await updater.add_artifact(
            [new_data_part(outcome.model_dump(mode="json"))], name=OUTCOME_ARTIFACT_NAME
        )
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Refuse: a call completes within one request, so no task is ever left to cancel."""
        raise NotImplementedError("calls are short and synchronous; nothing to cancel")


def create_a2a_app(
    *,
    handler: OutcomeHandler,
    card: AgentCard,
    audience: str,
    trust: TrustStore,
    allowed_callers: frozenset[str],
    lifespan: Callable[[], AbstractAsyncContextManager[None]] | None = None,
) -> Any:
    """Starlette app: signed card (public), JSON-RPC endpoint (bearer JWT), health check.

    Authentication runs as Starlette middleware inside the app; trace context wraps the whole
    app so a refused request is still traced.
    """
    request_handler = DefaultRequestHandler(
        agent_executor=OutcomeExecutor(handler),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )

    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @asynccontextmanager
    async def app_lifespan(_app: Starlette) -> AsyncGenerator[None]:
        if lifespan is None:
            yield
            return
        async with lifespan():
            yield

    app = Starlette(
        routes=[
            *create_agent_card_routes(card),
            *create_jsonrpc_routes(request_handler, DEFAULT_RPC_URL),
            Route(HEALTH_PATH, health),
        ],
        middleware=[
            Middleware(
                AuthenticationMiddleware,
                backend=MeshTokenBackend(
                    audience=audience, trust=trust, allowed_issuers=allowed_callers
                ),
                on_error=unauthorized_response,
            )
        ],
        lifespan=app_lifespan,
    )
    return TraceContextMiddleware(app)
