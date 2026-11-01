"""Command line entry point: keys, services, questions, kill switch, catalog and evals."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pydantic_ai
import uvicorn

from agent_mesh.config import (
    CATALOG_AGENT,
    COORDINATOR,
    ORDERS_AGENT,
    Settings,
    UnknownSettingError,
    get_settings,
)
from agent_mesh.control.kill_switch import KillSwitch
from agent_mesh.domain.catalog import write_catalog
from agent_mesh.evals.cases import load_cases
from agent_mesh.evals.export import export_dataset, export_predictions
from agent_mesh.evals.runner import run_suite
from agent_mesh.observability.tracing import TraceContextMiddleware, setup_tracing
from agent_mesh.orders_api import create_orders_app
from agent_mesh.security.keys import generate_keys
from agent_mesh.services import (
    ALL_PRINCIPALS,
    build_catalog_specialist,
    build_orders_specialist,
    coordinator_app,
    specialist_app,
)
from agent_mesh.user_client import ask_coordinator

ORDERS_API = "orders-api"
"""Service name of the mock order API. Not a principal: it holds no keys and signs nothing."""
SERVICE_NAMES = (COORDINATOR, CATALOG_AGENT, ORDERS_AGENT, ORDERS_API)
"""Everything `agent-mesh serve` can run, one name per docker compose service."""
DEFAULT_GOLDEN_CASES_DIR = Path("evals/golden")
"""Relative to the working directory: the repo root locally, `/app` in the image."""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-mesh", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    keys = commands.add_parser("keys", help="manage signing keys")
    keys.add_subparsers(dest="keys_command", required=True).add_parser(
        "init", help="create missing keys and the JWKS file (idempotent)"
    )

    serve = commands.add_parser("serve", help="run one service")
    serve.add_argument("service", choices=SERVICE_NAMES)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, required=True)

    ask = commands.add_parser("ask", help="ask the coordinator a question")
    ask.add_argument("text", nargs="?", default="")
    ask.add_argument("--customer", default="cus-fr-001")
    ask.add_argument("--conversation", default="cli")
    ask.add_argument("--confirm", choices=("yes", "no"), help="answer a pending confirmation")

    kill = commands.add_parser("kill", help="stop routing to an agent")
    kill.add_argument("agent", choices=(CATALOG_AGENT, ORDERS_AGENT))
    kill.add_argument("--ttl", type=float, help="seconds until the agent comes back on its own")
    kill.add_argument("--reason", default="")
    revive = commands.add_parser("revive", help="route to an agent again")
    revive.add_argument("agent", choices=(CATALOG_AGENT, ORDERS_AGENT))

    catalog = commands.add_parser("catalog", help="synthetic catalog")
    generate = catalog.add_subparsers(dest="catalog_command", required=True).add_parser(
        "generate", help="regenerate the catalog file from its seed"
    )
    generate.add_argument("--output", type=Path, help="defaults to the packaged catalog file")

    evals = commands.add_parser("eval", help="run the golden suites; exit 1 on any failure")
    evals.add_argument("--cases-dir", type=Path, default=DEFAULT_GOLDEN_CASES_DIR)
    evals.add_argument(
        "--export-dir",
        type=Path,
        help="also write the dataset and predictions for llm-regression-detector",
    )
    return parser


def _keys_init(settings: Settings, _args: argparse.Namespace) -> int:
    """Create missing keys and rewrite the JWKS file."""
    generate_keys(settings.keys_dir, ALL_PRINCIPALS)
    print(f"keys ready in {settings.keys_dir}")
    return 0


def build_app(settings: Settings, service: str) -> Any:
    """The ASGI app for one service, with tracing set up under the service's name."""
    setup_tracing(
        service,
        otlp_endpoint=settings.otlp_endpoint,
        capture_message_content=settings.capture_message_content,
    )
    if service == ORDERS_API:
        return TraceContextMiddleware(create_orders_app())
    if service == CATALOG_AGENT:
        return specialist_app(settings, build_catalog_specialist(settings))
    if service == ORDERS_AGENT:
        return specialist_app(settings, build_orders_specialist(settings))
    return coordinator_app(settings)


def _serve(settings: Settings, args: argparse.Namespace) -> int:
    """Serve one service with uvicorn until stopped."""
    uvicorn.run(build_app(settings, args.service), host=args.host, port=args.port)
    return 0


def _ask(settings: Settings, args: argparse.Namespace) -> int:
    """Ask the coordinator over A2A and print the outcome JSON."""
    confirm = None if args.confirm is None else args.confirm == "yes"
    outcome = asyncio.run(
        ask_coordinator(
            settings,
            args.text,
            customer_id=args.customer,
            conversation_id=args.conversation,
            confirm=confirm,
        )
    )
    print(outcome.model_dump_json(indent=2))
    return 0


def _kill(settings: Settings, args: argparse.Namespace) -> int:
    """Kill an agent in the shared kill switch file."""
    KillSwitch(settings.kill_switch_path).kill(args.agent, ttl_seconds=args.ttl, reason=args.reason)
    until = f" for {args.ttl:g}s" if args.ttl else " until revived"
    print(f"{args.agent} killed{until}")
    return 0


def _revive(settings: Settings, args: argparse.Namespace) -> int:
    """Clear an agent's kill switch entry."""
    KillSwitch(settings.kill_switch_path).revive(args.agent)
    print(f"{args.agent} revived")
    return 0


def _catalog_generate(settings: Settings, args: argparse.Namespace) -> int:
    """Regenerate the catalog file from its fixed seed."""
    output_path: Path = args.output or settings.catalog_path
    write_catalog(output_path)
    print(f"catalog written to {output_path}")
    return 0


def _eval(settings: Settings, args: argparse.Namespace) -> int:
    """Run every golden suite in process; exit 1 if any case fails."""
    suite_paths = sorted(args.cases_dir.glob("*.json"))
    if not suite_paths:
        print(f"no golden cases in {args.cases_dir}", file=sys.stderr)
        return 1
    failed_count = 0
    for path in suite_paths:
        cases = load_cases(path)
        results = asyncio.run(run_suite(cases, settings.model))
        passed_count = sum(r.passed for r in results)
        failed_count += len(results) - passed_count
        print(f"{path.stem}: {passed_count}/{len(results)} passed")
        for result in results:
            if not result.passed:
                print(f"  FAIL {result.case.id}: {'; '.join(result.failures)}")
        if args.export_dir is not None:
            args.export_dir.mkdir(parents=True, exist_ok=True)
            export_dataset(cases, args.export_dir / f"{path.stem}.dataset.json")
            export_predictions(results, args.export_dir / f"{path.stem}.predictions.jsonl")
    return 1 if failed_count else 0


COMMANDS: dict[str, Callable[[Settings, argparse.Namespace], int]] = {
    "keys": _keys_init,
    "serve": _serve,
    "ask": _ask,
    "kill": _kill,
    "revive": _revive,
    "catalog": _catalog_generate,
    "eval": _eval,
}


def main(argv: Sequence[str] | None = None) -> int:
    """Run one command and return its exit code (1 means a failed eval, 2 a bad setting).

    Pydantic AI's startup banner and httpx's per-request INFO lines are silenced so that the
    output of `ask` and `eval` stays machine-readable.
    """
    args = _build_parser().parse_args(argv)
    pydantic_ai.BANNER_ENABLED = False
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        settings = get_settings()
    except UnknownSettingError as error:
        print(error, file=sys.stderr)
        return 2
    return COMMANDS[args.command](settings, args)


if __name__ == "__main__":
    sys.exit(main())
