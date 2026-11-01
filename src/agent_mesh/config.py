"""Runtime settings, read from `AGENT_MESH_*` environment variables."""

from __future__ import annotations

import difflib
import os
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

CATALOG_AGENT = "catalog-agent"
"""Principal name of the catalog specialist: its agent card name, key owner and JWT `iss`."""
ORDERS_AGENT = "orders-agent"
"""Principal name of the orders specialist: its agent card name, key owner and JWT `iss`."""
COORDINATOR = "coordinator"
"""Principal name of the coordinator: its agent card name, key owner and JWT `iss`."""
DEMO_IDENTITY_PROVIDER = "demo-idp"
"""Issuer of end-user tokens in the demo. Production would trust a real identity provider."""

HEALTH_PATH = "/healthz"
"""Liveness probe every service serves for docker compose; public and never traced."""

PACKAGE_DATA_DIR = Path(__file__).parent / "data"
"""Shipped inside the wheel, so the catalog works the same from a checkout or an image."""


class Settings(BaseSettings):
    """Every knob of every service. Compose sets them per container; defaults run on localhost."""

    model_config = SettingsConfigDict(env_prefix="AGENT_MESH_", extra="ignore")

    model: str = "offline"
    """`offline` runs scripted models with no key and no download. Any Pydantic AI model name
    (for example `google:gemini-2.5-flash`, which reads `GOOGLE_API_KEY`) switches to a real LLM."""

    keys_dir: Path = Path(".keys")
    """Private keys and `jwks.json`, written by `agent-mesh keys init`."""
    kill_switch_path: Path = Path(".state/kill_switch.json")
    """Shared by the coordinator and the specialists, which all check it."""
    catalog_path: Path = PACKAGE_DATA_DIR / "catalog.json"
    """Catalog file the catalog MCP server indexes; `agent-mesh catalog generate` rewrites it."""

    orders_api_url: str = "http://localhost:8100"
    """Base URL of the mock order API, reached only by the orders MCP server."""
    catalog_agent_url: str = "http://localhost:8001"
    orders_agent_url: str = "http://localhost:8002"
    coordinator_url: str = "http://localhost:8000"
    """Agent URLs double as JWT audiences: a token minted for one agent is refused by another."""

    otlp_endpoint: str | None = None
    """OTLP/HTTP traces endpoint, for example `http://jaeger:4318/v1/traces`. None disables export."""

    capture_message_content: bool = False
    """Opt-in: record prompts and answers on spans (redacted). Off by default because shopping
    conversations carry personal data and GDPR minimisation favours not collecting it."""

    service_token_ttl_seconds: int = 60
    """Request tokens cover one call; 60 s absorbs clock skew without making a leak useful."""
    confirmation_ttl_seconds: int = 600
    """How long a customer's "yes" stays valid between the proposal and the filing."""


class UnknownSettingError(ValueError):
    """An `AGENT_MESH_*` environment variable that matches no setting."""


def check_unknown_settings(environ: Mapping[str, str]) -> None:
    """Raise on any `AGENT_MESH_*` variable that maps to no settings field.

    pydantic-settings ignores unknown environment variables even with `extra="forbid"`, so a
    misspelled or renamed variable would silently keep its default. Matching is
    case-insensitive, like pydantic-settings' own lookup.
    """
    prefix = Settings.model_config.get("env_prefix", "").upper()
    known = {f"{prefix}{field_name}".upper() for field_name in Settings.model_fields}
    # An alias replaces the prefixed name entirely in pydantic-settings, so it counts as is.
    known |= {
        alias.upper()
        for field in Settings.model_fields.values()
        for alias in (field.alias, field.validation_alias)
        if isinstance(alias, str)
    }
    unknown = sorted(
        key for key in environ if key.upper().startswith(prefix) and key.upper() not in known
    )
    if not unknown:
        return
    lines: list[str] = []
    for key in unknown:
        closest = difflib.get_close_matches(key.upper(), sorted(known), n=1)
        hint = f" (did you mean {closest[0]}?)" if closest else ""
        lines.append(f"  {key}{hint}")
    raise UnknownSettingError(f"unknown {prefix}* environment variables:\n" + "\n".join(lines))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Settings from the environment, read once per process, after rejecting unknown
    `AGENT_MESH_*` variables."""
    check_unknown_settings(os.environ)
    return Settings()
