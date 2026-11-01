---
status: accepted
date: 2026-10-20
---

# Human confirmation before any write, with an action-bound token

## Context and Problem Statement

Filing a return is the only write in the mesh. A model can be tricked or simply wrong about which item, order or quantity the customer meant. How does a write happen without letting the model decide on its own?

## Considered Options

* Give the model the write tool and trust the prompt
* Ask the customer to confirm, then let the model call the write tool
* The model only proposes; after the customer confirms, code calls a write tool the model cannot see, with a token bound to that exact action

## Decision Outcome

Chosen option: "The model only proposes; after the customer confirms, code calls a write tool the model cannot see, with a token bound to that exact action", because the confirmation then covers one specific order, item and quantity, and no prompt can reach the write path. Checkout and payment are deliberately not built yet: they need the same pattern with payment mandates (AP2) and are planned for 1.1 (CHANGELOG).

### Consequences

* Good, because the orders specialist returns `CLARIFY` with a `pending_action`, and the coordinator graph pauses with `interrupt()` until `Coordinator.confirm(approved=True)` (`coordinator/graph.py`).
* Good, because `create_return_request` is hidden from the model's tool list and needs a confirmation token whose `action_hash` matches the proposal (`mcp_servers/orders_server.py`, `security/tokens.py`).
* Good, because a confirmation token files at most one return (replay cache in `orders_server.py`) and expires after `confirmation_ttl_seconds` = 600 (`config.py`).
* Good, because the full flow is tested over A2A (`test_return_needs_confirmation_then_files`).
* Bad, because every write costs the customer one extra turn.
* Bad, because the paused run lives in the coordinator's memory (`_paused_runs` in `coordinator/graph.py`), so a restart loses pending confirmations.
