"""OpenTelemetry setup shared by every service.

- Pydantic AI's own instrumentation emits GenAI semantic-convention spans (`gen_ai.*`) for the
  specialists' model and tool calls. Content capture follows `capture_message_content`.
- W3C `traceparent` is injected on every outgoing A2A call and extracted on every incoming one,
  so one user question is one trace across the coordinator and both specialists.
- `RedactingSpanExporter` scrubs string attributes and event attributes before export, as a
  second line of defence behind "do not capture content".

Attribute names the coordinator sets follow the GenAI conventions the trace-to-golden pipeline
reads: `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.conversation.id`,
`gen_ai.input.messages` / `gen_ai.output.messages` (JSON strings, opt-in), `error.type`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import Event, ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.util.types import AttributeValue
from pydantic_ai import Agent
from pydantic_ai.models.instrumented import InstrumentationSettings
from starlette.types import ASGIApp, Receive, Scope, Send

from agent_mesh.config import HEALTH_PATH
from agent_mesh.security.redaction import redact

TRACE_CONTRACT_VERSION = "1.0.0"
"""Version of the trace contract in docs/architecture.md that trace-to-golden pins. Bump the
major for a removed or renamed attribute or placeholder, the minor for an added one."""
TRACE_CONTRACT_VERSION_ATTRIBUTE = "agent_mesh.trace_contract.version"
"""Resource attribute carrying `TRACE_CONTRACT_VERSION` on every exported span."""
OUTCOME_ATTRIBUTE = "agent_mesh.outcome"
"""Outcome kind on the coordinator span; trace-to-golden turns it into `expected_category`."""
INJECTION_EVENT = "agent_mesh.injection_screen"
"""Span event with the screening rules that fired, never the flagged text itself."""


def _redact_value(value: AttributeValue) -> AttributeValue:
    """Redact a string or every string of a sequence; numbers and booleans carry no PII."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, Sequence) and not isinstance(value, bytes):
        return [redact(v) if isinstance(v, str) else v for v in value]  # type: ignore[return-value]
    return value


def _redact_attributes(
    attributes: Mapping[str, AttributeValue] | None,
) -> dict[str, AttributeValue]:
    return {k: _redact_value(v) for k, v in (attributes or {}).items()}


class RedactingSpanExporter(SpanExporter):
    """Wraps another exporter and redacts every string attribute on spans and span events."""

    def __init__(self, inner: SpanExporter) -> None:
        self._inner = inner

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Export redacted copies; finished spans are read-only, so they are rebuilt."""
        return self._inner.export([self._redacted_copy(s) for s in spans])

    @staticmethod
    def _redacted_copy(span: ReadableSpan) -> ReadableSpan:
        return ReadableSpan(
            name=span.name,
            context=span.context,
            parent=span.parent,
            resource=span.resource,
            attributes=_redact_attributes(span.attributes),
            events=[
                Event(e.name, _redact_attributes(e.attributes), e.timestamp) for e in span.events
            ],
            links=span.links,
            kind=span.kind,
            status=span.status,
            start_time=span.start_time,
            end_time=span.end_time,
            instrumentation_scope=span.instrumentation_scope,
        )

    def shutdown(self) -> None:
        """Shut the wrapped exporter down."""
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Flush the wrapped exporter."""
        return self._inner.force_flush(timeout_millis)


def setup_tracing(
    service_name: str,
    *,
    otlp_endpoint: str | None,
    capture_message_content: bool,
    span_exporter: SpanExporter | None = None,
) -> TracerProvider:
    """Install a global tracer provider and turn on Pydantic AI instrumentation.

    `span_exporter` overrides OTLP, which tests use to capture spans in memory. Every exporter
    is wrapped in `RedactingSpanExporter`, so no code path exports unredacted spans.
    """
    resource = Resource.create(
        {SERVICE_NAME: service_name, TRACE_CONTRACT_VERSION_ATTRIBUTE: TRACE_CONTRACT_VERSION}
    )
    provider = TracerProvider(resource=resource)
    inner = span_exporter or (OTLPSpanExporter(endpoint=otlp_endpoint) if otlp_endpoint else None)
    if inner is not None:
        provider.add_span_processor(BatchSpanProcessor(RedactingSpanExporter(inner)))
    trace.set_tracer_provider(provider)
    Agent.instrument_all(
        InstrumentationSettings(
            tracer_provider=provider,
            include_content=capture_message_content,
            include_binary_content=False,
        )
    )
    return provider


def trace_headers() -> dict[str, str]:
    """W3C `traceparent` (and `tracestate`) for the current span, to send on an outgoing call."""
    headers: dict[str, str] = {}
    propagate.inject(headers)
    return headers


def messages_attribute(role: str, text: str) -> str:
    """One GenAI message list as the JSON string the spec allows when structured attributes
    are not available in the SDK."""
    return json.dumps([{"role": role, "parts": [{"type": "text", "content": text}]}])


class TraceContextMiddleware:
    """ASGI middleware: continue the caller's trace from its `traceparent` header.

    The span is named after the route template (`GET /customers/{customer_id}/orders`), never
    the raw path, so ids in URLs stay out of traces. This is also why the mesh does not use
    `opentelemetry-instrumentation-asgi`: it records the full path in `url.path`. Health probes
    are not traced: they run every few seconds and would bury real traces.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app
        self._tracer = trace.get_tracer("agent_mesh.server")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] == HEALTH_PATH:
            await self._app(scope, receive, send)
            return
        carrier = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        context = propagate.extract(carrier)
        method = scope["method"]
        with self._tracer.start_as_current_span(
            method, context=context, kind=trace.SpanKind.SERVER
        ) as span:
            await self._app(scope, receive, send)
            route = getattr(scope.get("route"), "path", None)
            if route is not None:
                span.update_name(f"{method} {route}")
