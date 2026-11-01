"""Startup check for misspelled or renamed `AGENT_MESH_*` environment variables."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from agent_mesh import cli
from agent_mesh.config import UnknownSettingError, check_unknown_settings, get_settings


@pytest.fixture
def fresh_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_misspelled_variable_names_the_closest_setting() -> None:
    with pytest.raises(
        UnknownSettingError, match=r"AGENT_MESH_MODLE \(did you mean AGENT_MESH_MODEL\?\)"
    ):
        check_unknown_settings({"AGENT_MESH_MODLE": "offline", "AGENT_MESH_MODEL": "offline"})


def test_every_unknown_variable_is_listed() -> None:
    with pytest.raises(UnknownSettingError) as error:
        check_unknown_settings({"AGENT_MESH_ZZZ": "1", "AGENT_MESH_OTLP_ENDPIONT": "x"})
    message = str(error.value)
    assert "AGENT_MESH_ZZZ" in message
    assert "did you mean AGENT_MESH_OTLP_ENDPOINT?" in message


def test_known_and_unrelated_variables_pass() -> None:
    check_unknown_settings(
        {
            "AGENT_MESH_MODEL": "offline",
            "agent_mesh_keys_dir": "keys",
            "AGENT_MESHY": "not ours",
            "OTEL_INSTRUMENTATION_A2A_SDK_ENABLED": "false",
        }
    )


@pytest.mark.usefixtures("fresh_settings_cache")
def test_get_settings_rejects_a_misspelled_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_MESH_CAPTURE_CONTENT", "true")
    with pytest.raises(UnknownSettingError, match="AGENT_MESH_CAPTURE_MESSAGE_CONTENT"):
        get_settings()


@pytest.mark.usefixtures("fresh_settings_cache")
def test_cli_exits_2_on_a_misspelled_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AGENT_MESH_MODLE", "offline")
    assert cli.main(["revive", "orders-agent"]) == 2
    assert "did you mean AGENT_MESH_MODEL?" in capsys.readouterr().err
