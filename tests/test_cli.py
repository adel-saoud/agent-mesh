"""The command line, with servers and the network replaced by fakes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_mesh import cli
from agent_mesh.config import Settings
from agent_mesh.contracts import Language, NotFound
from agent_mesh.control.kill_switch import KillSwitch


@pytest.fixture(autouse=True)
def cli_settings(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> Settings:
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    return settings


def test_keys_init(capsys: pytest.CaptureFixture[str], settings: Settings) -> None:
    assert cli.main(["keys", "init"]) == 0
    assert (settings.keys_dir / "jwks.json").exists()
    assert "keys ready" in capsys.readouterr().out


def test_kill_and_revive(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    switch = KillSwitch(settings.kill_switch_path)
    assert cli.main(["kill", "orders-agent", "--ttl", "30"]) == 0
    assert switch.is_killed("orders-agent")
    assert "for 30s" in capsys.readouterr().out
    assert cli.main(["revive", "orders-agent"]) == 0
    assert not switch.is_killed("orders-agent")
    cli.main(["kill", "catalog-agent"])
    assert "until revived" in capsys.readouterr().out


def test_catalog_generate(tmp_path: Path) -> None:
    output_path = tmp_path / "catalog.json"
    assert cli.main(["catalog", "generate", "--output", str(output_path)]) == 0
    assert len(json.loads(output_path.read_text())) == 60


def test_ask_prints_the_outcome(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, Any] = {}

    async def fake_ask(_settings: Settings, text: str, **kwargs: Any) -> NotFound:
        seen.update(kwargs, text=text)
        return NotFound(language=Language.EN, message="nothing")

    monkeypatch.setattr(cli, "ask_coordinator", fake_ask)
    assert cli.main(["ask", "--confirm", "yes", "--customer", "cus-ae-001"]) == 0
    assert seen["confirm"] is True
    assert seen["customer_id"] == "cus-ae-001"
    assert json.loads(capsys.readouterr().out)["kind"] == "NOT_FOUND"


@pytest.mark.parametrize("service", cli.SERVICE_NAMES)
def test_serve_builds_each_service(monkeypatch: pytest.MonkeyPatch, service: str) -> None:
    served: list[tuple[Any, str, int]] = []

    def fake_setup_tracing(*_args: object, **_kwargs: object) -> None:
        """The real one installs a global tracer provider, which test_tracing owns."""

    def fake_run(app: Any, host: str, port: int) -> None:
        served.append((app, host, port))

    monkeypatch.setattr(cli, "setup_tracing", fake_setup_tracing)
    monkeypatch.setattr(cli.uvicorn, "run", fake_run)
    assert cli.main(["serve", service, "--port", "9000"]) == 0
    assert served[0][1:] == ("127.0.0.1", 9000)
    assert callable(served[0][0])


def test_eval_exports_and_fails_on_a_regression(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cases_dir = tmp_path / "golden"
    cases_dir.mkdir()
    case = {
        "id": "wrong-kind",
        "agent": "catalog-agent",
        "customer_id": "cus-fr-001",
        "input_text": "Do you sell laptops?",
        "topic": "unknown_product",
        "expected": {"kind": "ANSWER"},
    }
    (cases_dir / "catalog-agent.json").write_text(json.dumps([case]))
    export_dir = tmp_path / "out"
    assert cli.main(["eval", "--cases-dir", str(cases_dir), "--export-dir", str(export_dir)]) == 1
    assert "FAIL wrong-kind" in capsys.readouterr().out
    assert (export_dir / "catalog-agent.dataset.json").exists()
    assert (export_dir / "catalog-agent.predictions.jsonl").exists()


def test_eval_without_cases(tmp_path: Path) -> None:
    assert cli.main(["eval", "--cases-dir", str(tmp_path)]) == 1
