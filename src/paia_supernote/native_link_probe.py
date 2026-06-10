from __future__ import annotations

import inspect
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import supernotelib


@dataclass(slots=True)
class NativeLinkProbeResult:
    status: str
    real_note_writes_allowed: bool
    reason: str
    evidence: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def probe_native_links(
    *,
    quick_fixture: Path | None = None,
    target_fixture: Path | None = None,
) -> NativeLinkProbeResult:
    evidence = [
        f"supernotelib version: {getattr(supernotelib, '__version__', 'unknown')}",
        *_public_link_symbols(),
    ]
    if quick_fixture is None or target_fixture is None:
        return NativeLinkProbeResult(
            status="blocked",
            real_note_writes_allowed=False,
            reason=(
                "fixture notebooks are required before native index links "
                "can be validated"
            ),
            evidence=evidence,
        )
    if not quick_fixture.exists() or not target_fixture.exists():
        return NativeLinkProbeResult(
            status="blocked",
            real_note_writes_allowed=False,
            reason=(
                "fixture notebook paths must exist before native index links "
                "can be validated"
            ),
            evidence=evidence,
        )
    return NativeLinkProbeResult(
        status="blocked",
        real_note_writes_allowed=False,
        reason=(
            "native link creation is blocked because no validated Supernote "
            "cross-notebook link constructor is available in this codebase"
        ),
        evidence=evidence,
    )


def _public_link_symbols() -> list[str]:
    symbols: list[str] = []
    for module_name in ("supernotelib", "supernotelib.parser", "supernotelib.manipulator"):
        try:
            module = __import__(module_name, fromlist=["*"])
        except Exception as exc:
            symbols.append(f"{module_name}: import failed: {exc}")
            continue
        link_names = [
            name
            for name in dir(module)
            if "link" in name.lower() and not name.startswith("_")
        ]
        if not link_names:
            symbols.append(f"{module_name}: no public link symbols")
            continue
        for name in link_names:
            value = getattr(module, name)
            try:
                signature = str(inspect.signature(value))
            except (TypeError, ValueError):
                signature = " (no signature)"
            symbols.append(f"{module_name}.{name}{signature}")
    return symbols
