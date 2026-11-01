---
status: accepted
date: 2026-10-20
---

# A2A between agents, with the official a2a-sdk

## Context and Problem Statement

The coordinator and the two specialists run as separate services (`docker-compose.yml`). They need a wire protocol for agent-to-agent calls, and each side needs a server and a client implementation. Which protocol, and which library?

## Considered Options

* Plain HTTP with a project-specific JSON schema
* A2A 1.0 with `fasta2a` serving the Pydantic AI specialists
* A2A 1.0 with the official `a2a-sdk` on both sides
* A framework's built-in A2A server

## Decision Outcome

Chosen option: "A2A 1.0 with the official `a2a-sdk` on both sides", because A2A gives agent cards, declared security schemes and task semantics that a custom schema would have to reinvent, and the official SDK was the only option that worked end to end. `fasta2a` failed against the `a2a-sdk` client, and open-source LangGraph has no A2A server of its own.

### Consequences

* Good, because agent cards can be signed with the SDK's own signing utilities (`a2a-sdk[signing]` in `pyproject.toml`, used in `security/cards.py`).
* Good, because the card, JSON-RPC path and protocol version come from SDK constants rather than hand-written values (CHANGELOG, Unreleased).
* Good, because the end-to-end path is covered over real A2A: `tests/test_a2a_mesh.py` runs signed cards, bearer tokens, both specialists and a confirmation.
* Bad, because `fasta2a` is not usable as a server: its task timestamps have no timezone and the `a2a-sdk` client rejects them (README, Limits). The specialists wrap Pydantic AI in an `a2a-sdk` executor instead (`a2a_server.py`).
* Bad, because the SDK traces its internal event queue. Its instrumentation is turned off in the image (`OTEL_INSTRUMENTATION_A2A_SDK_ENABLED=false` in `Dockerfile`; see [0008](0008-versioned-trace-contract-with-redaction.md)).
* Bad, because the A2A SDK rejects a message with empty text, so a bare confirmation sends "yes" or "no" (`user_client.py`, `test_bare_confirmation_over_a2a`).
