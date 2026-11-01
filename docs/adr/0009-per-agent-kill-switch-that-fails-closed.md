---
status: accepted
date: 2026-10-20
---

# Per-agent kill switch, checked by caller and callee, failing closed

## Context and Problem Statement

When a specialist misbehaves (bad model release, abuse, an upstream outage), an operator needs to stop traffic to it in seconds, without a deploy. Where does the switch live, who checks it, and what happens when its state cannot be read?

## Considered Options

* Remove the specialist from the coordinator's configuration and redeploy
* A feature-flag service or Redis key checked by the coordinator only
* A shared JSON file checked by both the coordinator and the specialist, treating an unreadable file as "everything killed"

## Decision Outcome

Chosen option: "A shared JSON file checked by both the coordinator and the specialist, treating an unreadable file as everything killed", because checking at the callee stops a killed agent even when a caller has a stale view or skips the check, and failing closed means a corrupted switch can only stop traffic, never silently restore it. A file keeps the demo free of extra infrastructure; `KillSwitch` hides the storage so it can move to Redis or a flag service without touching callers.

### Consequences

* Good, because `agent-mesh kill <agent> [--ttl SECONDS]` takes effect without a restart, and a kill without TTL never expires by accident (`control/kill_switch.py`, `test_kill_and_revive`).
* Good, because a killed agent answers `UNAVAILABLE`, never an error page, so the customer gets a clear reply ([0004](0004-typed-outcome-contract.md), `specialists/base.py`).
* Bad, because a file works only while every service shares one volume (`docker-compose.yml`); several hosts need the Redis or flag-service backend, which is not built.
* Bad, because a corrupted file stops every agent at once, a deliberate outage in exchange for never running a killed agent.
