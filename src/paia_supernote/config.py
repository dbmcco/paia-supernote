"""Configuration helpers for the Supernote Cloud change ledger.

The ledger allowlist is opt-in. When the explicit allowlist config key is
unset, the ledger falls back to the existing ``folio_sync_notebooks`` setting
so legacy configurations keep working unchanged.

This module only resolves config and performs no Cloud I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: Explicit allowlist config key. When present, it fully controls which
#: notebooks the ledger processes.
LEDGER_ALLOWLIST_KEY = "cloud_change_ledger_notebooks"

#: Legacy key the ledger falls back to when no explicit allowlist is set, so
#: existing deployments keep their current behavior.
LEGACY_FALLBACK_KEY = "folio_sync_notebooks"


def resolve_ledger_notebooks(config: Mapping[str, Any]) -> list[str]:
    """Resolve the ordered list of notebooks the ledger should process.

    Returns the explicit ``cloud_change_ledger_notebooks`` list when set;
    otherwise falls back to ``folio_sync_notebooks`` so legacy configs are
    preserved when no allowlist is configured.

    Each name is normalized to a bare stem by stripping a trailing ``.note``
    suffix so the resolved allowlist agrees with the Cloud poller watch set
    (which compares file-name stems) and the read/write contract
    canonicalization. Casing is preserved for display.
    """
    explicit = config.get(LEDGER_ALLOWLIST_KEY)
    if explicit is not None:
        return [_normalize(name) for name in explicit if _normalize(name)]
    return [
        _normalize(name)
        for name in config.get(LEGACY_FALLBACK_KEY) or []
        if _normalize(name)
    ]


def notebook_is_ledger_allowlisted(
    config: Mapping[str, Any], notebook: str
) -> bool:
    """Case-insensitive membership test against the resolved ledger allowlist.

    A trailing ``.note`` suffix on the requested name is stripped via the same
    ``_normalize`` helper used by ``resolve_ledger_notebooks`` so the helper
    agrees with the read/write contracts when called with a suffixed name.
    """
    target = _normalize(notebook).casefold()
    if not target:
        return False
    return any(
        str(name).strip().casefold() == target
        for name in resolve_ledger_notebooks(config)
    )


def _strip_note_suffix(name: str) -> str:
    """Strip a trailing ``.note`` suffix (case-insensitive on the suffix)."""
    return name[:-5] if name.lower().endswith(".note") else name


def _normalize(name: Any) -> str:
    """Strip whitespace and a trailing ``.note`` suffix from a notebook name."""
    return _strip_note_suffix(str(name).strip())
