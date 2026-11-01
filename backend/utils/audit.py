"""Audit trail recorder. Each pipeline step appends a single entry so
the UI can show judges exactly what the agent did, in order."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Dict, Any


def _now_iso() -> str:
    """Return current UTC time in ISO8601 — used for every audit ts."""
    return datetime.now(timezone.utc).isoformat()


class AuditTrail:
    """Append-only list of {step, detail, ts}. Returned to the user
    in every API response so the agent's reasoning is inspectable."""

    def __init__(self) -> None:
        self._entries: List[Dict[str, Any]] = []

    def add(self, step: str, detail: str) -> None:
        """Append one step. Keep step names short, detail human-readable."""
        self._entries.append(
            {"step": step, "detail": detail, "ts": _now_iso()}
        )

    def to_list(self) -> List[Dict[str, Any]]:
        """Return a copy of the trail for the API response."""
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)
