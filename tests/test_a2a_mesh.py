"""End to end over real A2A: signed cards, bearer tokens, both specialists, confirmation."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH

from agent_mesh.config import CATALOG_AGENT, ORDERS_AGENT, Settings
from agent_mesh.contracts import Answer, Clarify, NotFound, Unavailable, UnavailableReason
from agent_mesh.coordinator.clients import A2ASpecialistClient
from agent_mesh.coordinator.graph import Coordinator
from agent_mesh.security.keys import KeyUse, TrustStore, load_signing_key
from agent_mesh.security.tokens import mint_token
from agent_mesh.services import build_coordinator
from agent_mesh.user_client import ask_coordinator
from tests.conftest import HostRouterTransport


async def test_catalog_question_over_a2a(a2a_coordinator: Coordinator) -> None:
    outcome = await a2a_coordinator.ask(
        "How loud is the blender KW-BLD-001?", customer_id="cus-fr-001", conversation_id="c1"
    )
    assert isinstance(outcome, Answer)
    assert outcome.citations[0].doc_id == "KW-BLD-001"


async def test_other_customers_order_is_not_found(a2a_coordinator: Coordinator) -> None:
    outcome = await a2a_coordinator.ask(
        "Where is my order ORD-2001?", customer_id="cus-fr-001", conversation_id="c1"
    )
    assert isinstance(outcome, NotFound)


async def test_return_needs_confirmation_then_files(a2a_coordinator: Coordinator) -> None:
    question = "I want to return the kettle KW-KET-002 from ORD-1001"
    first = await a2a_coordinator.ask(question, customer_id="cus-fr-001", conversation_id="c2")
    assert isinstance(first, Clarify)
    assert first.pending_action is not None
    done = await a2a_coordinator.confirm(
        customer_id="cus-fr-001", conversation_id="c2", approved=True
    )
    assert isinstance(done, Answer)
    assert done.citations[0].doc_id.startswith("RET-")


async def test_user_to_coordinator_over_a2a(
    settings: Settings, mesh_transport: httpx.AsyncBaseTransport
) -> None:
    outcome = await ask_coordinator(
        settings,
        "Where is my order ORD-1001?",
        customer_id="cus-fr-001",
        conversation_id="conv-1",
        transport=mesh_transport,
    )
    assert isinstance(outcome, Answer)


async def test_bare_confirmation_over_a2a(
    settings: Settings, mesh_transport: httpx.AsyncBaseTransport
) -> None:
    proposed = await ask_coordinator(
        settings,
        "return KW-KET-002 from ORD-1001",
        customer_id="cus-fr-001",
        conversation_id="conv-2",
        transport=mesh_transport,
    )
    assert isinstance(proposed, Clarify)
    filed = await ask_coordinator(
        settings,
        "",
        customer_id="cus-fr-001",
        conversation_id="conv-2",
        confirm=True,
        transport=mesh_transport,
    )
    assert isinstance(filed, Answer)
    assert filed.citations[0].doc_id.startswith("RET-")


async def test_specialist_refuses_missing_and_wrong_tokens(
    settings: Settings, specialist_apps: dict[str, Any]
) -> None:
    transport = httpx.ASGITransport(app=specialist_apps["orders-agent"])
    async with httpx.AsyncClient(transport=transport, base_url="http://orders-agent") as http:
        card = await http.get(AGENT_CARD_WELL_KNOWN_PATH)
        assert card.status_code == 200
        assert (await http.post("/", json={})).status_code == 401
        wrong_audience = mint_token(
            load_signing_key(settings.keys_dir, "coordinator", KeyUse.TOKEN),
            audience="http://catalog-agent",
            subject="cus-fr-001",
            ttl_seconds=60,
        )
        response = await http.post(
            "/", json={}, headers={"Authorization": f"Bearer {wrong_audience}"}
        )
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == 'Bearer error="invalid_token"'
        wrong_issuer = mint_token(
            load_signing_key(settings.keys_dir, "demo-idp", KeyUse.TOKEN),
            audience="http://orders-agent",
            subject="cus-fr-001",
            ttl_seconds=60,
        )
        response = await http.post(
            "/", json={}, headers={"Authorization": f"Bearer {wrong_issuer}"}
        )
        assert response.status_code == 401


@pytest.mark.parametrize("agent", ["catalog-agent", "orders-agent"])
async def test_unreachable_specialist_is_unavailable(
    settings: Settings, trust: TrustStore, specialist_apps: dict[str, Any], agent: str
) -> None:
    question_by_agent = {"catalog-agent": "price of KW-KET-001", "orders-agent": "status ORD-1001"}
    endpoints = {CATALOG_AGENT: settings.catalog_agent_url, ORDERS_AGENT: settings.orders_agent_url}
    client = A2ASpecialistClient(
        token_key=load_signing_key(settings.keys_dir, "coordinator", KeyUse.TOKEN),
        trust=trust,
        agent_urls={**endpoints, agent: "http://nowhere"},
        token_ttl_seconds=60,
        transport=HostRouterTransport(specialist_apps),
    )
    outcome = await build_coordinator(settings, client).ask(
        question_by_agent[agent], customer_id="cus-fr-001", conversation_id="c3"
    )
    assert isinstance(outcome, Unavailable)
    assert outcome.reason is UnavailableReason.UPSTREAM_ERROR
