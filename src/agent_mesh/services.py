"""Wiring: build each service of the mesh from `Settings`.

Kept apart from the domain code so tests can assemble the same objects with in-process
transports, and so the deployed topology is readable in one place:

    user --(A2A, demo-idp token)--> coordinator --(A2A, coordinator token)--> catalog-agent
                                                \\-------------------------> orders-agent
    catalog-agent --(MCP stdio)--> catalog server
    orders-agent  --(MCP stdio)--> orders server --(HTTP)--> mock order API
"""

from __future__ import annotations

from importlib.metadata import version
from typing import Any

from a2a.server.agent_execution import RequestContext
from a2a.types import AgentCard, AgentSkill
from google.protobuf.json_format import MessageToDict

from agent_mesh.a2a_server import OutcomeHandler, create_a2a_app, specialist_handler
from agent_mesh.config import (
    CATALOG_AGENT,
    COORDINATOR,
    DEMO_IDENTITY_PROVIDER,
    ORDERS_AGENT,
    Settings,
)
from agent_mesh.contracts import Outcome
from agent_mesh.control.kill_switch import KillSwitch
from agent_mesh.coordinator.clients import A2ASpecialistClient, SpecialistClient
from agent_mesh.coordinator.graph import Coordinator, CoordinatorConfig
from agent_mesh.coordinator.router import build_router
from agent_mesh.security.auth import verified_claims
from agent_mesh.security.cards import build_card, sign_card
from agent_mesh.security.keys import KeyUse, TrustStore, load_signing_key
from agent_mesh.specialists.agents import (
    OrdersSpecialist,
    build_catalog_agent,
    build_orders_agent,
    catalog_toolset,
    orders_toolset,
)
from agent_mesh.specialists.base import PydanticAISpecialist

ALL_PRINCIPALS = [COORDINATOR, CATALOG_AGENT, ORDERS_AGENT, DEMO_IDENTITY_PROVIDER]
"""Every key owner. `agent-mesh keys init` writes a card key and a token key for each."""

_AGENT_SKILLS: dict[str, list[AgentSkill]] = {
    CATALOG_AGENT: [
        AgentSkill(
            id="product-questions",
            name="Product questions",
            description="Features, prices (EUR, AED) and comparisons from the Kettlewick catalog.",
            tags=["catalog", "products"],
            examples=["How loud is the KW-BLD-001 blender?", "Quel est le prix de la bouilloire ?"],
        )
    ],
    ORDERS_AGENT: [
        AgentSkill(
            id="orders-and-returns",
            name="Orders and returns",
            description="Order status and return requests for the signed-in customer.",
            tags=["orders", "returns"],
            examples=["Where is my order ORD-1001?", "Je veux retourner ORD-1001"],
        )
    ],
    COORDINATOR: [
        AgentSkill(
            id="shopping-assistant",
            name="Shopping and after-sales assistant",
            description="Routes product, order and return questions to the right specialist.",
            tags=["assistant"],
            examples=["Is the KW-KET-002 kettle still under warranty for order ORD-1001?"],
        )
    ],
}

_AGENT_DESCRIPTIONS = {
    CATALOG_AGENT: "Answers Kettlewick product questions from the catalog.",
    ORDERS_AGENT: "Order status and human-confirmed returns for the signed-in customer.",
    COORDINATOR: "Kettlewick shopping and after-sales assistant.",
}


def agent_url(settings: Settings, principal: str) -> str:
    """Where a principal is served, which is also the audience of tokens sent to it."""
    return {
        CATALOG_AGENT: settings.catalog_agent_url,
        ORDERS_AGENT: settings.orders_agent_url,
        COORDINATOR: settings.coordinator_url,
    }[principal]


def signed_card(settings: Settings, principal: str) -> AgentCard:
    """The principal's agent card, signed with its card key.

    The card version is the installed package version, so it cannot drift from pyproject.toml.
    """
    card = build_card(
        name=principal,
        description=_AGENT_DESCRIPTIONS[principal],
        url=agent_url(settings, principal),
        version=version("agent-mesh"),
        skills=_AGENT_SKILLS[principal],
    )
    return sign_card(card, load_signing_key(settings.keys_dir, principal, KeyUse.CARD))


def build_catalog_specialist(settings: Settings, mcp_client: Any = None) -> PydanticAISpecialist:
    """`mcp_client` replaces the stdio MCP subprocess, for tests and evals."""
    agent = build_catalog_agent(settings, catalog_toolset(settings, mcp_client))
    return PydanticAISpecialist(CATALOG_AGENT, agent, KillSwitch(settings.kill_switch_path))


def build_orders_specialist(settings: Settings, mcp_client: Any = None) -> OrdersSpecialist:
    """`mcp_client` replaces the stdio MCP subprocess, for tests and evals."""
    toolset = orders_toolset(settings, mcp_client)
    return OrdersSpecialist(
        build_orders_agent(settings, toolset), toolset, KillSwitch(settings.kill_switch_path)
    )


def specialist_app(settings: Settings, specialist: PydanticAISpecialist) -> Any:
    """A2A app for a specialist. Only the coordinator may call it."""
    principal = specialist.name
    return create_a2a_app(
        handler=specialist_handler(specialist),
        card=signed_card(settings, principal),
        audience=agent_url(settings, principal),
        trust=TrustStore.from_dir(settings.keys_dir),
        allowed_callers=frozenset({COORDINATOR}),
        lifespan=specialist.mcp_servers_lifespan,
    )


def build_coordinator(
    settings: Settings, specialist_client: SpecialistClient | None = None
) -> Coordinator:
    """Over A2A by default; `specialist_client` swaps in another transport, such as in-process calls.

    The coordinator signs confirmations with its token key, the same key the orders MCP server
    trusts for issuer `coordinator`.
    """
    trust = TrustStore.from_dir(settings.keys_dir)
    token_key = load_signing_key(settings.keys_dir, COORDINATOR, KeyUse.TOKEN)
    specialist_client = specialist_client or A2ASpecialistClient(
        token_key=token_key,
        trust=trust,
        agent_urls={
            CATALOG_AGENT: settings.catalog_agent_url,
            ORDERS_AGENT: settings.orders_agent_url,
        },
        token_ttl_seconds=settings.service_token_ttl_seconds,
    )
    return Coordinator(
        specialist_client=specialist_client,
        router=build_router(settings.model),
        kill_switch=KillSwitch(settings.kill_switch_path),
        confirmation_key=token_key,
        config=CoordinatorConfig(
            model_name=settings.model,
            capture_message_content=settings.capture_message_content,
            confirmation_ttl_seconds=settings.confirmation_ttl_seconds,
        ),
    )


def coordinator_handler(coordinator: Coordinator) -> OutcomeHandler:
    """Message metadata `{"confirm": true|false}` answers a pending confirmation; anything else
    is a new question. The A2A `context_id` is the conversation id."""

    async def handle(context: RequestContext) -> Outcome:
        customer_id = str(verified_claims(context.call_context)["sub"])
        conversation_id = context.context_id or "default"
        metadata: dict[str, Any] = (
            MessageToDict(context.message.metadata) if context.message is not None else {}
        )
        if "confirm" in metadata:
            return await coordinator.confirm(
                customer_id=customer_id,
                conversation_id=conversation_id,
                approved=bool(metadata["confirm"]),
            )
        return await coordinator.ask(
            context.get_user_input(), customer_id=customer_id, conversation_id=conversation_id
        )

    return handle


def coordinator_app(settings: Settings, coordinator: Coordinator | None = None) -> Any:
    """A2A app for the coordinator. End users call it with a token from the demo identity
    provider; in production that would be the company's identity provider."""
    return create_a2a_app(
        handler=coordinator_handler(coordinator or build_coordinator(settings)),
        card=signed_card(settings, COORDINATOR),
        audience=settings.coordinator_url,
        trust=TrustStore.from_dir(settings.keys_dir),
        allowed_callers=frozenset({DEMO_IDENTITY_PROVIDER}),
    )
