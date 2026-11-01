"""The end-user side: call the coordinator over A2A the way a chat front end would.

The demo identity provider (`demo-idp`) signs the user token; `sub` is the customer id. The
coordinator's card is verified before the first message, like every hop in the mesh.
"""

from __future__ import annotations

import httpx
from a2a.client import A2ACardResolver
from a2a.helpers import new_message, new_text_part
from a2a.types import Role

from agent_mesh.config import COORDINATOR, DEMO_IDENTITY_PROVIDER, Settings
from agent_mesh.contracts import Outcome
from agent_mesh.coordinator.clients import send_for_outcome
from agent_mesh.observability.tracing import trace_headers
from agent_mesh.security.auth import MeshTokenCredentials, client_call_context
from agent_mesh.security.cards import verify_card
from agent_mesh.security.keys import KeyUse, TrustStore, load_signing_key


async def ask_coordinator(
    settings: Settings,
    text: str,
    *,
    customer_id: str,
    conversation_id: str,
    confirm: bool | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Outcome:
    """Ask a question, or answer a pending confirmation when `confirm` is set.

    A2A rejects a message with empty text, so a bare confirmation is sent as "yes" or "no".
    """
    if confirm is not None and not text:
        text = "yes" if confirm else "no"
    trust = TrustStore.from_dir(settings.keys_dir)
    credentials = MeshTokenCredentials(
        load_signing_key(settings.keys_dir, DEMO_IDENTITY_PROVIDER, KeyUse.TOKEN),
        ttl_seconds=settings.service_token_ttl_seconds,
    )
    message = new_message([new_text_part(text)], context_id=conversation_id, role=Role.ROLE_USER)
    if confirm is not None:
        message.metadata.update({"confirm": confirm})
    context = client_call_context(audience=settings.coordinator_url, subject=customer_id)
    async with httpx.AsyncClient(headers=trace_headers(), transport=transport, timeout=60) as http:
        card = await A2ACardResolver(http, settings.coordinator_url).get_agent_card()
        verify_card(card, expected_principal=COORDINATOR, trust=trust)
        outcome = await send_for_outcome(http, card, message, credentials, context)
    if outcome is None:
        raise RuntimeError("coordinator replied without an outcome")
    return outcome
