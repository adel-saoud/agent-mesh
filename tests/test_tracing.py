"""One trace per question across A2A hops, GenAI attributes, redaction on export."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent_mesh.config import CATALOG_AGENT, HEALTH_PATH, ORDERS_AGENT, Settings
from agent_mesh.coordinator.clients import A2ASpecialistClient
from agent_mesh.observability.tracing import (
    OUTCOME_ATTRIBUTE,
    TRACE_CONTRACT_VERSION,
    TRACE_CONTRACT_VERSION_ATTRIBUTE,
    TraceContextMiddleware,
    setup_tracing,
)
from agent_mesh.orders_api import create_orders_app
from agent_mesh.security.keys import KeyUse, TrustStore, load_signing_key
from agent_mesh.services import build_coordinator
from tests.conftest import HostRouterTransport


@pytest.fixture(scope="module")
def exporter() -> InMemorySpanExporter:
    memory = InMemorySpanExporter()
    setup_tracing("test", otlp_endpoint=None, capture_message_content=True, span_exporter=memory)
    return memory


def _flush() -> None:

    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    provider.force_flush()


async def test_one_trace_redacted_with_genai_attributes(
    exporter: InMemorySpanExporter,
    settings: Settings,
    trust: TrustStore,
    specialist_apps: dict[str, Any],
) -> None:
    exporter.clear()
    client = A2ASpecialistClient(
        token_key=load_signing_key(settings.keys_dir, "coordinator", KeyUse.TOKEN),
        trust=trust,
        agent_urls={
            CATALOG_AGENT: settings.catalog_agent_url,
            ORDERS_AGENT: settings.orders_agent_url,
        },
        token_ttl_seconds=60,
        transport=HostRouterTransport(specialist_apps),
    )
    coordinator = build_coordinator(
        settings.model_copy(update={"capture_message_content": True}), client
    )
    await coordinator.ask(
        "Where is ORD-1001? Reach me at camille.martin@example.com or +33 6 12 34 56 78",
        customer_id="cus-fr-001",
        conversation_id="conv-trace",
    )
    _flush()
    spans: list[ReadableSpan] = list(exporter.get_finished_spans())
    root = next(s for s in spans if s.name == "invoke_agent coordinator")
    attrs = dict(root.attributes or {})
    assert attrs["gen_ai.operation.name"] == "invoke_agent"
    assert attrs["gen_ai.provider.name"] == "offline"
    assert attrs["gen_ai.conversation.id"] == "conv-trace"
    assert attrs[OUTCOME_ATTRIBUTE] == "ANSWER"
    assert root.resource.attributes[TRACE_CONTRACT_VERSION_ATTRIBUTE] == TRACE_CONTRACT_VERSION
    assert "cus-fr-001" not in json.dumps({k: str(v) for k, v in attrs.items()})
    message = json.loads(str(attrs["gen_ai.input.messages"]))[0]["parts"][0]["content"]
    assert "<EMAIL_ADDRESS>" in message
    assert "<PHONE_NUMBER>" in message
    assert "example.com" not in message

    root_trace = root.context.trace_id if root.context else None
    server_spans = [s for s in spans if s.name == "POST /"]
    assert server_spans, "the specialist's server span must be exported"
    assert all(s.context is not None and s.context.trace_id == root_trace for s in server_spans)
    model_spans = [s for s in spans if (s.attributes or {}).get("gen_ai.operation.name") == "chat"]
    assert model_spans, "Pydantic AI must emit GenAI chat spans for the specialist"
    assert all(s.context is not None and s.context.trace_id == root_trace for s in model_spans)


async def test_server_spans_use_route_templates_and_skip_health(
    exporter: InMemorySpanExporter,
) -> None:
    exporter.clear()
    app = TraceContextMiddleware(create_orders_app())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://orders-api"
    ) as http:
        assert (await http.get(HEALTH_PATH)).status_code == 200
        await http.get("/customers/cus-fr-001/orders")
    _flush()
    assert [s.name for s in exporter.get_finished_spans()] == [
        "GET /customers/{customer_id}/orders"
    ]


def test_architecture_doc_states_the_trace_contract_version() -> None:
    doc = (Path(__file__).parents[1] / "docs" / "architecture.md").read_text()
    assert f"Version **{TRACE_CONTRACT_VERSION}**" in doc
