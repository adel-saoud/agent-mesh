# Architecture Decision Records

One file per significant decision, in [MADR 4.0.0](https://adr.github.io/madr/) format. Facts and measurements cite the code or [results/manifest.json](../../results/manifest.json). A record stays `proposed` until the maintainer confirms its reasoning, then becomes `accepted`; a replaced decision becomes `superseded by NNNN`.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-langgraph-coordinator-pydantic-ai-specialists.md) | LangGraph for the coordinator, Pydantic AI for the specialists | accepted |
| [0002](0002-a2a-with-the-official-a2a-sdk.md) | A2A between agents, with the official a2a-sdk | accepted |
| [0003](0003-mcp-servers-over-stdio-with-customer-id-in-meta.md) | MCP tool servers as stdio subprocesses, customer id in `_meta` | accepted |
| [0004](0004-typed-outcome-contract.md) | A typed outcome contract with six kinds | accepted |
| [0005](0005-human-confirmation-before-writes.md) | Human confirmation before any write, with an action-bound token | accepted |
| [0006](0006-per-request-tokens-on-top-of-signed-agent-cards.md) | Per-request tokens on top of signed agent cards | accepted |
| [0007](0007-deterministic-offline-eval-gate.md) | Deterministic offline eval gate in CI, live model as a report | accepted |
| [0008](0008-versioned-trace-contract-with-redaction.md) | Versioned trace contract, with redaction before export | accepted |
| [0009](0009-per-agent-kill-switch-that-fails-closed.md) | Per-agent kill switch, checked by caller and callee, failing closed | accepted |
| [0010](0010-rule-based-injection-screening.md) | Rule-based prompt-injection screening instead of a classifier | accepted |
