---
status: accepted
date: 2026-10-24
---

# Versioned trace contract, with redaction before export

## Context and Problem Statement

trace-to-golden turns the mesh's OpenTelemetry traces into new golden cases, so it depends on span names, attributes and redaction placeholders. Shopping conversations also carry personal data (emails, phone numbers, card numbers, IBANs). How do traces stay useful to another repo without leaking personal data or breaking that repo silently?

## Decision Drivers

* trace-to-golden must be able to pin what it reads.
* No personal data in exported spans, and the customer id never in an attribute.
* A readable trace: one per question, without library noise.

## Considered Options

* Default instrumentation (`opentelemetry-instrumentation-asgi`, a2a-sdk's own tracing), content recorded
* Presidio redaction in every service
* A documented, versioned contract: own server middleware, GenAI attributes on the coordinator span, content off by default, regex redaction with checksums on every exported span

## Decision Outcome

Chosen option: "A documented, versioned contract", because a consumer in another repo needs a version to pin, and every control in it removes data rather than relying on downstream filtering. The ASGI instrumentation records the raw URL path (which holds the customer id), a2a-sdk's tracing floods the trace, and Presidio would need a spaCy model per service image for four entity types that regexes with checksums already catch.

### Consequences

* Good, because the contract is written once ([docs/architecture.md, Trace contract](../architecture.md#trace-contract)), every span carries `agent_mesh.trace_contract.version` = 1.0.0, and a test fails if the doc and `TRACE_CONTRACT_VERSION` disagree (`observability/tracing.py`, `test_architecture_doc_states_the_trace_contract_version`).
* Good, because turning off a2a-sdk's tracing cut a question's trace from about 175 spans to 22 (`docs/architecture.md`, Tracing, measured on the compose stack on 2026-10-20).
* Good, because server spans use the route template, never the raw path, and every exported string attribute is redacted (`TraceContextMiddleware`, `RedactingSpanExporter`, `test_one_trace_redacted_with_genai_attributes`).
* Good, because message content is recorded only with `AGENT_MESH_CAPTURE_MESSAGE_CONTENT=true` (`config.py`).
* Bad, because regex redaction misses names and street addresses (README, Limits).
* Bad, because the contract does not yet cover the stdio hop to the MCP servers ([0003](0003-mcp-servers-over-stdio-with-customer-id-in-meta.md)), and any rename now needs a version bump and a CHANGELOG entry in both repos.
