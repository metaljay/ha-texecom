"""Names for keypad users: the option that maps user numbers to names
("3 = Sam"), and showing a name wherever the panel says "User 3"."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .const import CONF_USER_NAMES

# "3 = Sam", "3: Sam", "User 3 = Sam"...
_LINE = re.compile(r"^\s*(?:user\s*)?(\d{1,4})\s*[=:,-]\s*(\S.*?)\s*$", re.IGNORECASE)
# How the panel drivers name a keypad user in "changed by".
_LABEL = re.compile(r"^User (\d+)$")


def parse_user_names(text: str) -> dict[str, str] | None:
    """{"3": "Sam"} from one "number = name" per line; None if a line
    doesn't fit that."""
    names: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        if not (match := _LINE.match(line)):
            return None
        names[str(int(match.group(1)))] = match.group(2)
    return names


def format_user_names(names: Mapping[str, str]) -> str:
    """The option as it's shown for editing: one "3 = Sam" per line."""
    return "\n".join(f"{number} = {name}" for number, name in sorted(names.items(), key=lambda kv: int(kv[0])))


def user_name(options: Mapping[str, Any], number: int) -> str:
    """User 3's name, or "User 3"."""
    return options.get(CONF_USER_NAMES, {}).get(str(number), f"User {number}")


def with_user_name(options: Mapping[str, Any], label: str | None) -> str | None:
    """A driver's "changed by" with the user's name: "User 3" -> "Sam".
    Anything else (a zone, "Home Assistant") is left as it is."""
    if label and (match := _LABEL.match(label)):
        return user_name(options, int(match.group(1)))
    return label
