"""Per-agent kill switch backed by a small JSON file.

Semantics chosen to fail closed:

- A kill has no expiry unless a TTL is given, so an agent never comes back on its own by accident.
- If the state file exists but cannot be read or parsed, every agent counts as killed.
- A missing file means nothing is killed (the normal state of a fresh install).
- Both the coordinator and the agent itself check the switch, so a caller that skips the check
  still gets UNAVAILABLE.

A file keeps the demo dependency-free; behind `KillSwitch` it could be Redis or a feature-flag
service without touching callers.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, TypeAdapter, ValidationError


class KillSwitchEntry(BaseModel):
    """One killed agent. `killed_at` and `reason` are for whoever reads the file during an incident."""

    killed_at: float
    expires_at: float | None = None
    reason: str = ""


_KillSwitchStateAdapter = TypeAdapter(dict[str, KillSwitchEntry])


class KillSwitch:
    """Read and write the shared kill switch file."""

    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        self._path = path
        self._clock = clock

    def _read(self) -> dict[str, KillSwitchEntry] | None:
        """Current state, or None when the file is unreadable (callers then fail closed)."""
        if not self._path.exists():
            return {}
        try:
            return _KillSwitchStateAdapter.validate_json(self._path.read_bytes())
        except (OSError, ValidationError):
            return None

    def is_killed(self, agent: str) -> bool:
        """True while a kill is active, and always when the file cannot be read."""
        state = self._read()
        if state is None:
            return True
        entry = state.get(agent)
        if entry is None:
            return False
        return entry.expires_at is None or entry.expires_at > self._clock()

    def kill(self, agent: str, *, ttl_seconds: float | None = None, reason: str = "") -> None:
        """Stop routing to `agent`, for `ttl_seconds` or until revived."""
        now = self._clock()
        state = self._read() or {}
        state[agent] = KillSwitchEntry(
            killed_at=now,
            expires_at=None if ttl_seconds is None else now + ttl_seconds,
            reason=reason,
        )
        self._write(state)

    def revive(self, agent: str) -> None:
        """Route to `agent` again."""
        state = self._read() or {}
        state.pop(agent, None)
        self._write(state)

    def _write(self, state: dict[str, KillSwitchEntry]) -> None:
        """Write atomically so a reader never sees a half-written file (which would kill all)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self._path.with_suffix(".tmp")
        temporary_path.write_bytes(_KillSwitchStateAdapter.dump_json(state, indent=2))
        os.replace(temporary_path, self._path)
