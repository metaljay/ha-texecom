"""Keeps the panel's recent messages for the diagnostics download, so a user
with a panel, module or device we haven't seen can share what it sends in
one file. Always on, in memory only, with a fixed size. The panel's messages
never carry codes (keypad users are numbers)."""

from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Any

from . import protocol as P

RECENT = 1000  # messages kept, newest last
UNRECOGNISED = 50  # different unrecognised messages kept (each with a count)
KINDS = 100  # different kinds of message kept (each with a count), so rare ones outlast RECENT


def describe(m: dict[str, Any]) -> str:
    """A decoded message in words, for the diagnostics."""
    if m["kind"] == "log":
        return f"log: {P.describe_log(m)}"
    fields = ", ".join(f"{k} {v}" for k, v in m.items() if k not in ("kind", "raw", "data"))
    return f"{m['kind']}: {fields}" if fields else m["kind"]


def unrecognised(m: dict[str, Any]) -> str | None:
    """What makes a message one the driver doesn't know (the key it's counted
    under), or None if it knows it."""
    kind = m["kind"]
    if kind in ("unknown", "debug"):
        return f"{kind} message {m['raw'][:2]}"
    if kind == "log" and (m["type"] not in P.LOG_EVENT_NAMES or m["group"] not in P.LOG_GROUP_NAMES):
        return f"log type {m['type']}, group {m['group']}"
    if kind == "area" and m["state"].startswith("unknown"):
        return f"area state {m['state_code']}"
    if kind == "user" and m["method"].startswith("unknown"):
        return f"user method {m['method']}"
    return None


def kind(m: dict[str, Any]) -> str:
    """The kind of message, finer than its type (the key it's counted under
    in message_kinds): a wireless detector's hourly check-in, say, stays
    listed after it has dropped out of the recent messages."""
    match m["kind"]:
        case "zone":
            return f"zone bits {m['raw'].split()[-1]}"
        case "area":
            return f"area state {m['state_code']}"
        case "log":
            return f"log type {m['type']}, group {m['group']}"
        case "output":
            return f"output location {m['location']}"
        case "user":
            return f"user method {m['method']}"
        case other:
            return f"{other} message {m['raw'][:2]}"


def _tally(table: dict[str, dict[str, Any]], key: str, entry: dict[str, str], limit: int) -> None:
    if key in table:
        table[key]["count"] += 1
        table[key]["last"] = entry
    elif len(table) < limit:
        table[key] = {"count": 1, "first": entry, "last": entry}


class Capture:
    def __init__(self) -> None:
        self.recent: deque[dict[str, str]] = deque(maxlen=RECENT)
        self.unrecognised: dict[str, dict[str, Any]] = {}
        self.kinds: dict[str, dict[str, Any]] = {}

    def add(self, m: dict[str, Any]) -> None:
        entry = {
            "at": datetime.now().isoformat(sep=" ", timespec="milliseconds"),
            "raw": m.get("raw", ""),
            "read": describe(m),
        }
        self.recent.append(entry)
        _tally(self.kinds, kind(m), entry, KINDS)
        if (key := unrecognised(m)) is not None:
            _tally(self.unrecognised, key, entry, UNRECOGNISED)

    def diagnostics(self) -> dict[str, Any]:
        return {
            "unrecognised_messages": self.unrecognised,
            "message_kinds": dict(sorted(self.kinds.items())),
            "recent_messages": list(self.recent),
        }
