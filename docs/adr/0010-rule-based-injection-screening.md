---
status: accepted
date: 2026-10-20
---

# Rule-based prompt-injection screening instead of a classifier

## Context and Problem Statement

The mesh reads untrusted text twice: the customer's question and third-party product reviews returned by catalog search. OWASP LLM01:2025 states there is no fool-proof prevention for prompt injection. What should screen that text, and how much should the design rely on it?

## Considered Options

* No screening; rely only on the structural controls
* A classifier model on every input
* Regex rules in English and French, as detection and telemetry, with structural controls doing the real containment

## Decision Outcome

Chosen option: "Regex rules in English and French, as detection and telemetry", because the damage is already bounded elsewhere (identity from verified tokens, deterministic return eligibility, human-confirmed writes, replies parsed into the outcome contract), so the screen only needs to catch common phrasings cheaply, answer `DECLINE` early and record which rule fired. A classifier would add a model and latency to every request for a control that still cannot be fool-proof.

### Consequences

* Good, because five rules (`override_instructions`, `role_hijack`, `fake_system_block`, `prompt_exfiltration`, `bulk_data_request`) run on the question and on retrieved reviews, and a flagged review is withheld (`security/screening.py`, `test_screen_flags_injections`, `test_injected_review_is_withheld`).
* Good, because a hit is recorded as the span event `agent_mesh.injection_screen` with the rule names only, never the flagged text (`coordinator/graph.py`, [0008](0008-versioned-trace-contract-with-redaction.md)).
* Good, because normal questions pass (`test_screen_passes_normal_questions`).
* Bad, because a paraphrased attack can get through (README, Limits), and other languages are not covered.
