---
status: accepted
date: 2026-10-20
---

# A typed outcome contract with six kinds

## Context and Problem Statement

Every agent answers the coordinator, and the coordinator answers the customer. If replies are free text, callers have to parse prose, a confused or compromised specialist can pass instructions upstream, and evals have nothing stable to score. What shape should every agent reply take?

## Considered Options

* Free text plus an HTTP status code
* A typed union of five kinds: `ANSWER`, `CLARIFY`, `DOC_GAP`, `NOT_FOUND`, `DECLINE`
* The same union plus `UNAVAILABLE`

## Decision Outcome

Chosen option: "The same union plus `UNAVAILABLE`", because callers and evals branch on a kind instead of parsing text, and an outage must never be scored as a correct refusal. The rules around the kinds follow from the same goal: another customer's order is `NOT_FOUND`, not `DECLINE`, so the reply does not confirm the order exists, and an `ANSWER` whose citations were not retrieved in the same run becomes `DOC_GAP`.

### Consequences

* Good, because the coordinator never forwards a specialist's free text: it parses the A2A artifact into `Outcome` and anything else becomes `UNAVAILABLE` (`contracts.py`, `coordinator/clients.py`).
* Good, because made-up citations are dropped before a reply leaves the specialist (`specialists/base.py`, `test_ground_drops_unretrieved_citations`).
* Good, because the eval gate scores the kind directly; all 25 golden cases check it (`results/manifest.json`, `runs[0].metrics`).
* Good, because merging several specialists' replies is a fixed precedence rule (`CLARIFY` > `DOC_GAP` > `NOT_FOUND` > `DECLINE` > `UNAVAILABLE`, `test_merge_precedence`).
* Bad, because adding a kind is a contract change for every agent, the eval export and the trace attribute `agent_mesh.outcome` ([0008](0008-versioned-trace-contract-with-redaction.md)).
* Bad, because a grounded answer with no citation left is downgraded to `DOC_GAP`, so a correct but uncited model answer is lost.
