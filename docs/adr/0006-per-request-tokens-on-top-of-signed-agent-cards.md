---
status: accepted
date: 2026-10-20
---

# Per-request tokens on top of signed agent cards

## Context and Problem Statement

A2A calls cross service boundaries. The caller needs to know it reached the real specialist, the specialist needs to know who is calling and for which customer, and a leaked credential should be close to useless. A signed agent card proves who the callee is, but not who the caller is. What authenticates each call?

## Considered Options

* Network isolation only (compose network, no auth)
* A static API key per service
* Signed agent cards plus a short-lived ES256 token per request

## Decision Outcome

Chosen option: "Signed agent cards plus a short-lived ES256 token per request", because the card lets the caller verify the callee, and a per-request token carries the caller (`iss`), the callee (`aud`) and the customer (`sub`) for one call only. Verification uses Starlette's `AuthenticationMiddleware` and token attachment uses a2a-sdk's `AuthInterceptor`, not custom auth plumbing, and each principal has separate card and token keys.

### Consequences

* Good, because a token is valid for one callee (`aud` = the callee's URL), for `service_token_ttl_seconds` = 60, and its `jti` is refused if seen twice (`config.py`, `security/tokens.py`).
* Good, because the customer id crosses the mesh only in the verified `sub`, never in a prompt (`security/tokens.py`; see [0003](0003-mcp-servers-over-stdio-with-customer-id-in-meta.md)).
* Good, because the caller refuses a specialist whose card does not verify and answers `UNAVAILABLE` (`coordinator/clients.py`), and a missing or wrong token is refused (`test_specialist_refuses_missing_and_wrong_tokens`).
* Bad, because in compose every service reads one shared key volume (`docker-compose.yml`). Production would give each service only its own private keys and publish public keys over a JWKS endpoint; that is not built.
* Bad, because the replay cache is in memory per process, so it does not survive a restart or span replicas.
