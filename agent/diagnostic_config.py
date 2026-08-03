"""Small, non-secret feature gates for diagnostic telemetry.

Diagnostics are opt-in so normal gateway startup and DB paths keep their existing
cost. Explicit HERMES_* overrides are intended for tests and one-off probes;
operator-facing configuration lives under ``observability`` in config.yaml.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home


_FALSE = {"0", "false", "no", "off", "disabled"}
_TRUE = {"1", "true", "yes", "on", "enabled"}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in _TRUE


def _load_yaml(path: Path) -> dict:
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ImportError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def enabled(name: str, *, env_name: str | None = None, default: bool = False) -> bool:
    """Return an opt-in diagnostic gate without making failures fatal."""
    env_name = env_name or "HERMES_" + name.upper().replace(".", "_")
    raw = os.environ.get(env_name)
    if raw is not None:
        if raw.strip().lower() in _FALSE:
            return False
        if raw.strip().lower() in _TRUE:
            return True

    config = _load_yaml(get_hermes_home() / "config.yaml")
    current: Any = config.get("observability", {})
    for part in name.split("."):
        if not isinstance(current, dict):
            return default
        current = current.get(part)
    if isinstance(current, dict):
        current = current.get("enabled", default)
    return _as_bool(current) if current is not None else default
