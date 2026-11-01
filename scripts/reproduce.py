"""Regenerate every number the README shows and write them to results/manifest.json.

    uv run python scripts/reproduce.py           # offline scripted models, no key, deterministic
    uv run python scripts/reproduce.py --live    # also the model set in AGENT_MESH_MODEL

The runs reuse the eval gate (`load_cases`, `run_suite`), so the manifest measures exactly what
CI checks. The live run is never needed for the default reproduction: it costs API calls and its
numbers drift with the provider's model. The live model name comes only from settings
(`AGENT_MESH_MODEL`), so switching models never means editing this script.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pydantic_ai

from agent_mesh.config import get_settings
from agent_mesh.domain.catalog import CATALOG_SEED, load_catalog
from agent_mesh.evals.cases import load_cases
from agent_mesh.evals.runner import run_suite

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN_CASES_DIR = REPO_ROOT / "evals" / "golden"
CATALOG_PATH = REPO_ROOT / "src" / "agent_mesh" / "data" / "catalog.json"
OFFLINE_MODELS_PATH = REPO_ROOT / "src" / "agent_mesh" / "llm" / "offline.py"
DEFAULT_MANIFEST_PATH = REPO_ROOT / "results" / "manifest.json"
RECORDED_PACKAGES = (
    "agent-mesh",
    "a2a-sdk",
    "langgraph",
    "mcp",
    "pydantic-ai-slim",
    "opentelemetry-sdk",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _git_state() -> dict[str, Any]:
    """HEAD commit, or `uncommitted` when the repo has none; `dirty` flags local changes."""
    git = shutil.which("git")
    if git is None:
        return {"commit": "uncommitted", "dirty": True}

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - fixed git arguments, no user input
            [git, *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
        )

    head = run("rev-parse", "HEAD")
    dirty = bool(run("status", "--porcelain").stdout.strip())
    commit = head.stdout.strip() if head.returncode == 0 else "uncommitted"
    return {"commit": commit, "dirty": dirty}


def _hardware() -> dict[str, Any]:
    summary: dict[str, Any] = {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
    }
    sysctl = shutil.which("sysctl")
    if platform.system() == "Darwin" and sysctl is not None:
        brand = subprocess.run(  # noqa: S603 - fixed sysctl arguments, no user input
            [sysctl, "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, check=False
        )
        summary["cpu"] = brand.stdout.strip()
    return summary


def _datasets() -> list[dict[str, Any]]:
    suites = [
        {"path": _relative(path), "sha256": _sha256(path), "cases": len(load_cases(path))}
        for path in sorted(GOLDEN_CASES_DIR.glob("*.json"))
    ]
    catalog = {
        "path": _relative(CATALOG_PATH),
        "sha256": _sha256(CATALOG_PATH),
        "products": len(load_catalog(CATALOG_PATH)),
    }
    return [*suites, catalog]


def _run_golden_suites(run_name: str, model: dict[str, str]) -> dict[str, Any]:
    """Pass count per suite, with the suite size as the sample size, and the failed case ids."""
    metrics: list[dict[str, Any]] = []
    failed_case_ids: list[str] = []
    for path in sorted(GOLDEN_CASES_DIR.glob("*.json")):
        results = asyncio.run(run_suite(load_cases(path), model["name"]))
        passed_count = sum(result.passed for result in results)
        metrics.append(
            {
                "name": "golden_cases_passed",
                "suite": path.stem,
                "value": passed_count,
                "n": len(results),
            }
        )
        failed_case_ids += [result.case.id for result in results if not result.passed]
        print(f"{run_name} {path.stem}: {passed_count}/{len(results)} passed")
    return {
        "name": run_name,
        "model": model,
        "metrics": metrics,
        "failed_case_ids": failed_case_ids,
    }


def build_manifest(*, live_model: str | None) -> dict[str, Any]:
    """Run the offline suites, and the live ones when `live_model` is set, and describe the run."""
    runs = [
        _run_golden_suites(
            "offline",
            {
                "name": "offline",
                "version": f"sha256:{_sha256(OFFLINE_MODELS_PATH)}",
                "source": _relative(OFFLINE_MODELS_PATH),
            },
        )
    ]
    if live_model is not None:
        runs.append(
            _run_golden_suites(
                "live", {"name": live_model, "version": "as served by the provider, not pinned"}
            )
        )
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "command": "uv run python scripts/reproduce.py" + (" --live" if live_model else ""),
        "git": _git_state(),
        "python": platform.python_version(),
        "packages": {name: version(name) for name in RECORDED_PACKAGES},
        "hardware": _hardware(),
        "seed": {"catalog": CATALOG_SEED},
        "datasets": _datasets(),
        "runs": runs,
    }


def main(argv: list[str] | None = None) -> int:
    """Write the manifest; exit 2 when `--live` is asked while `AGENT_MESH_MODEL` is `offline`.

    Provider credentials (for example `GOOGLE_API_KEY` for `google:` models) are read by
    Pydantic AI; a missing one fails the live run with the provider's own error.
    """
    parser = argparse.ArgumentParser(description="Regenerate results/manifest.json.")
    parser.add_argument(
        "--live", action="store_true", help="also run the golden suites on AGENT_MESH_MODEL"
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_MANIFEST_PATH)
    args = parser.parse_args(argv)
    live_model = get_settings().model if args.live else None
    if live_model == "offline":
        print("--live needs AGENT_MESH_MODEL set to a real model", file=sys.stderr)
        return 2
    pydantic_ai.BANNER_ENABLED = False
    logging.getLogger("httpx").setLevel(logging.WARNING)
    manifest = build_manifest(live_model=live_model)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    print(f"manifest written to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
