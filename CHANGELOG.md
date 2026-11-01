# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-11-01

Contains breaking changes. While the project is on 0.x, a breaking change is a minor bump.

### Added

- Startup check for `AGENT_MESH_*` environment variables: an unknown one stops startup with the closest valid name (exit code 2 from the CLI), since pydantic-settings would otherwise ignore it.
- Versioned trace contract (1.0.0) in `docs/architecture.md`, for trace-to-golden to pin. Every exported span carries it as the resource attribute `agent_mesh.trace_contract.version`.
- `scripts/reproduce.py` regenerates every README number into `results/manifest.json`; `--live` adds a run on the model set in `AGENT_MESH_MODEL`, the only place a model name is configured. A test keeps the README in line with the manifest.
- README Requirements: Python, uv, Docker Compose 2.24.0 with BuildKit, the pinned Jaeger version.

### Changed

- **Breaking:** an `AGENT_MESH_*` variable that matches no setting now fails startup instead of being ignored.
- **Breaking:** Python 3.12 is now the minimum (was 3.11). The project, the Docker image and CI run on Python 3.14.
- **Breaking (CLI):** `agent-mesh catalog generate --out` is now `--output`. No alias is kept.
- Docs: `docs/DECISIONS.md` replaced by ten Architecture Decision Records in MADR 4.0.0 format under `docs/adr/`, all accepted.
- Build backend pinned to `uv_build>=0.13.5,<0.14`; dev tools aligned on ruff 0.17, pyright 1.1.414, pytest 9 and pytest-cov 7.
- Ruff runs from `astral-sh/ruff-pre-commit` instead of a local hook; commit messages are checked by commitizen, and `cz bump` writes the version and the `v` tag; this file stays hand-written.
- CI split into pre-commit, tests and build jobs; the build job installs the wheel in a clean venv and runs `agent-mesh --help`.
- The live eval workflow runs on manual dispatch only, no longer nightly, on the model in the repository variable `AGENT_MESH_MODEL` (set it before the first run).
- Dependabot runs monthly with one grouped update per ecosystem, now including pre-commit hooks.
- `docs/ARCHITECTURE.md` renamed to `docs/architecture.md`, with a failure modes table.
- README restructured: why, try it, how it works, measured results, design decisions, limits and companion repos, with a recorded demo.
- `fastmcp-slim[client]` is a declared dependency: the specialists import its `StdioTransport` directly instead of relying on it arriving through `pydantic-ai-slim[mcp]`.
- Agent cards take their version from the installed package metadata instead of a hard-coded `0.1.0`.
- Library constants replace hand-written values: a2a-sdk's agent card path, JSON-RPC path and protocol version, and Starlette's ASGI types.
- Internal names made explicit across the package (for example `screen_for_injection`, `ground_citations`, `ExpectedOutcome`, `agent_urls`); unused parameters removed (`SpecialistDeps.today`, the catalog seed and BM25 tuning arguments, `mint_token`'s clock).

### Planned for 1.1

- Checkout with human approval, using AP2 checkout and payment mandates over A2A.
- Local models through Ollama behind a compose profile.

## [0.1.0]

Not published; the baseline for the changes above.

### Added

- Coordinator (LangGraph) and two specialists (Pydantic AI) over A2A 1.0, with MCP servers for catalog search and the mock order API.
- Typed outcome contract: `ANSWER`, `CLARIFY`, `DOC_GAP`, `NOT_FOUND`, `DECLINE`, `UNAVAILABLE`.
- Return requests with human confirmation and an action-bound confirmation token.
- Signed agent cards, per-request ES256 tokens with audience and replay checks.
- Prompt-injection screening (EN, FR) on input and retrieved reviews.
- OpenTelemetry GenAI tracing across A2A hops, with PII redaction before export.
- Per-agent kill switch with optional TTL.
- Golden eval suites per agent, a deterministic CI gate, and export to llm-regression-detector schema v2.
- Synthetic bilingual catalog (60 products) and seed orders for France and the UAE.
- `docker compose` stack with Jaeger, offline by default, Gemini when a key is set.
