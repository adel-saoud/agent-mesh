---
status: accepted
date: 2026-10-20
---

# MCP tool servers as stdio subprocesses, customer id in `_meta`

## Context and Problem Statement

Each specialist reaches its data through tools: catalog search, and the order API with one write (a return request). The tools must not be reachable by anything except their own specialist, and a tricked model must not be able to read another customer's orders. How should the tools be served, and how does the customer identity reach them?

## Decision Drivers

* Least privilege: a tool server reachable only by its agent.
* Broken object level authorization: the model must never choose whose data it reads.

## Considered Options

* Tools as plain Python functions inside the agent
* MCP servers as network services (streamable HTTP)
* MCP servers as stdio subprocesses of their specialist, customer id passed as a tool argument
* MCP servers as stdio subprocesses, customer id in the MCP request `_meta`

## Decision Outcome

Chosen option: "MCP servers as stdio subprocesses, customer id in the MCP request `_meta`", because stdio keeps each tool server off the network and inside its specialist's container, and `_meta` lets the agent attach the customer id from the verified token without the model ever seeing or setting it. MCP keeps the tools on a standard interface that Pydantic AI already speaks.

### Consequences

* Good, because no tool server listens on a port: `_stdio_transport` starts the server module as a subprocess (`specialists/agents.py`).
* Good, because `inject_customer_id` sets `agent_mesh/customer_id` in `_meta` from the request token (`specialists/agents.py`, `mcp_servers/orders_server.py`), and `test_other_customers_order_is_not_found` checks that another customer's order stays invisible.
* Good, because the subprocess gets only `AGENT_MESH_*`, `OTEL_*` and `PATH` variables, so API keys of the agent process are not passed down (`specialists/agents.py`).
* Bad, because the stdio hop carries no trace context: order API spans start their own trace ([docs/architecture.md, Trace contract](../architecture.md#trace-contract)).
* Bad, because each specialist process starts its tool server, so tools cannot be scaled or shared separately from the agent.
