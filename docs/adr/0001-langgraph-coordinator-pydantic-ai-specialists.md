---
status: accepted
date: 2026-10-20
---

# LangGraph for the coordinator, Pydantic AI for the specialists

## Context and Problem Statement

The mesh has two kinds of agent. The coordinator screens a question, routes it, calls specialists in parallel, merges their outcomes and pauses for a human "yes" before a write (`src/agent_mesh/coordinator/graph.py`). Each specialist is one model with a few tools and a typed output (`src/agent_mesh/specialists/base.py`). Which framework should each kind use: one framework for both, or the best fit for each role?

## Considered Options

* LangGraph for both
* Pydantic AI for both
* LangGraph for the coordinator, Pydantic AI for the specialists

## Decision Outcome

Chosen option: "LangGraph for the coordinator, Pydantic AI for the specialists", because the coordinator is a state machine that must pause and resume (LangGraph `interrupt()` and its checkpointer do this), while a specialist is a single typed tool-calling loop, which Pydantic AI expresses with the outcome contract as its `output_type` and ships an MCP client for. The A2A boundary between them means neither side depends on the other's framework.

### Consequences

* Good, because the confirm step is a LangGraph `interrupt()` resumed only by `Coordinator.confirm(approved=True)` (`coordinator/graph.py`), so a write cannot run without the resume call.
* Good, because a specialist's output is validated against the outcome union by Pydantic AI before it leaves the agent (`specialists/base.py`).
* Good, because the coordinator does not care how a specialist is built: `SpecialistClient` returns the same `Outcome` in process or over A2A (`coordinator/clients.py`).
* Bad, because two frameworks means two upgrade streams (`langgraph` and `pydantic-ai-slim` in `pyproject.toml`) and two sets of concepts for a contributor.
* Bad, because the LangGraph 1.2 type stubs are partial, so `graph.py` turns off one pyright check (`reportUnknownMemberType`).
