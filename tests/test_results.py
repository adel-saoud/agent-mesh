"""scripts/reproduce.py writes the manifest, and the README shows only what the manifest holds."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from agent_mesh.config import get_settings

REPO_ROOT = Path(__file__).parents[1]
MANIFEST_PATH = REPO_ROOT / "results" / "manifest.json"


def _load_reproduce() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "reproduce", REPO_ROOT / "scripts" / "reproduce.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _offline_run(manifest: dict[str, Any]) -> dict[str, Any]:
    return next(run for run in manifest["runs"] if run["name"] == "offline")


def test_reproduce_writes_a_complete_manifest(tmp_path: Path) -> None:
    output = tmp_path / "manifest.json"
    assert _load_reproduce().main(["--output", str(output)]) == 0
    manifest = json.loads(output.read_text())
    for key in ("generated_at", "git", "python", "packages", "hardware", "seed", "datasets"):
        assert manifest[key]
    run = _offline_run(manifest)
    assert run["failed_case_ids"] == []
    assert all(metric["value"] == metric["n"] for metric in run["metrics"])


def test_live_run_needs_a_real_model_from_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("AGENT_MESH_MODEL", raising=False)
    get_settings.cache_clear()
    try:
        assert _load_reproduce().main(["--live", "--output", str(tmp_path / "m.json")]) == 2
    finally:
        get_settings.cache_clear()


def test_readme_results_match_the_committed_manifest() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text())
    readme = (REPO_ROOT / "README.md").read_text()
    assert f"Last run: {manifest['generated_at'][:10]}, Python {manifest['python']}" in readme
    for metric in _offline_run(manifest)["metrics"]:
        row = f"| {metric['value']} | {metric['n']} |"
        assert f"[{metric['suite']}](evals/golden/{metric['suite']}.json) {row}" in readme
