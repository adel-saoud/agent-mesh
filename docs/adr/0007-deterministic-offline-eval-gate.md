---
status: accepted
date: 2026-10-20
---

# Deterministic offline eval gate in CI, live model as a report

## Context and Problem Statement

Changes to routing, the outcome contract or the guards can break the mesh in ways unit tests miss. Model output drifts between runs and costs money, so a gate on a live model would be flaky and would need a secret on every pull request. How does CI catch regressions, and where is answer quality scored?

## Considered Options

* Live-model evals on every pull request
* No eval gate, unit tests only
* Golden cases run against scripted offline models in CI, the live model run by hand as a report, answer quality scored by llm-regression-detector on an exported dataset

## Decision Outcome

Chosen option: "Golden cases run against scripted offline models in CI, the live model run by hand as a report, answer quality scored by llm-regression-detector on an exported dataset", because the deterministic checks (outcome kind, language, citations, decline reason, proposed write) are exactly what code can verify without a judge, and scoring answers belongs to the tool built for it rather than a second judge inside this repo.

### Consequences

* Good, because every golden case passes offline: catalog-agent 7 of 7, coordinator 8 of 8, orders-agent 10 of 10 (`results/manifest.json`, `runs[0].metrics`), and any failure fails the `eval-gate` job (`.github/workflows/ci.yml`).
* Good, because the live run needs no code change to switch models: it uses `AGENT_MESH_MODEL` and runs only on manual dispatch (`.github/workflows/live-eval.yml`).
* Good, because `agent-mesh eval --export-dir` writes a schema v2 dataset and predictions per suite that `lrd score` judges without re-running the mesh (`evals/export.py`, `test_export_matches_schema_v2`).
* Bad, because the gate proves the wiring and the guards, not answer quality: no live-model score is published yet (README, Results).
* Bad, because the scripted models must be updated whenever a prompt or flow changes (`llm/offline.py`), and their hash is recorded as the offline model version (`results/manifest.json`, `runs[0].model.version`).
