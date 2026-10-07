"""Protocol-independent panel model shared by the Connect and Crestron drivers.

Nothing in this file (or connect/, crestron/) imports Home Assistant, so the
drivers can be tested on their own.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Area states use Home Assistant's alarm_control_panel state strings.
DISARMED = "disarmed"
ARMING = "arming"
PENDING = "pending"  # entry delay
ARMED_AWAY = "armed_away"
ARMED_HOME = "armed_home"
ARMED_NIGHT = "armed_night"
TRIGGERED = "triggered"

ARMED_STATE_FOR_MODE = {"away": ARMED_AWAY, "home": ARMED_HOME, "night": ARMED_NIGHT}
ARM_MODES = ("away", "home", "night")

# How long entities keep showing the last known state after the connection
# drops. A SmartCom drops the session for about a minute whenever it reports
# an alarm (seen on a real panel), which is exactly when the alarm should stay
# visible; the panel connection sensor shows the real link state throughout.
OFFLINE_GRACE = 180.0


# Kept in capitals when a panel name is title-cased ("HALL PIR" -> "Hall PIR").
ACRONYMS = {"PIR", "PA", "CO", "CO2", "GSM", "UPS", "LED", "AC", "DC", "CCTV", "WC", "PSU", "UDL", "RF", "IR", "ATS"}


def nice_name(name: str) -> str:
    """Panels store names in capitals ("HOUSE"); show them in title case."""
    if not name.isupper():
        return name
    return " ".join(word if word in ACRONYMS else word.title() for word in name.split(" "))


class PanelError(Exception):
    """An arm/disarm request failed or the panel refused it."""


@dataclass
class PanelZone:
    number: int
    name: str
    panel_type: int | None = None  # Texecom zone type, when known (Connect)
    areas: list[int] = field(default_factory=list)
    state: str = "secure"  # secure | active | tamper | short
    bypassed: bool = False
    known: bool = False  # a state has been received since connecting

    @property
    def active(self) -> bool:
        return self.state == "active"

    @property
    def tampered(self) -> bool:
        return self.state in ("tamper", "short")


@dataclass
class PanelArea:
    number: int
    name: str
    state: str = DISARMED
    part_arm: int | None = None  # 1-3 while part armed, when known
    changed_by: str | None = None
    known: bool = False


@dataclass
class PanelInfo:
    model: str = "Premier Elite"
    zones: int | None = None
    firmware: str | None = None


class TexecomPanel(ABC):
    """A panel connection that keeps zones and areas up to date.

    part_arms maps the Home and Night modes to a panel part arm (1-3), or 0
    when the mode isn't offered. Listeners are called with no arguments after
    any change (zone, area, connection or info).
    """

    def __init__(self, part_arms: dict[str, int]) -> None:
        self.part_arms = {"home": int(part_arms.get("home") or 0), "night": int(part_arms.get("night") or 0)}
        self.zones: dict[int, PanelZone] = {}
        self.areas: dict[int, PanelArea] = {}
        self.info = PanelInfo()
        self.connected = False
        self.disconnected_since: float | None = time.monotonic()  # None while connected
        self.offline_grace = OFFLINE_GRACE
        self._grace_handle: asyncio.TimerHandle | None = None
        self.extra: dict[str, Any] = {}  # e.g. power readings (Connect)
        self._listeners: list[Callable[[], None]] = []
        # Arm mode last requested per area, used when the panel's report
        # doesn't say which mode it armed in.
        self._requested_mode: dict[int, str] = {}

    # ─── Listeners ──────────────────────────────────────────────────────────

    def add_listener(self, callback: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback) if callback in self._listeners else None

    def notify(self) -> None:
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:
                _LOGGER.exception("Error in panel listener")

    def set_connected(self, connected: bool) -> None:
        if self.connected == connected:
            return
        self.connected = connected
        if self._grace_handle:
            self._grace_handle.cancel()
            self._grace_handle = None
        if connected:
            self.disconnected_since = None
        else:
            self.disconnected_since = time.monotonic()
            # Re-evaluate availability once the grace period is over.
            with contextlib.suppress(RuntimeError):  # no running loop (tests)
                self._grace_handle = asyncio.get_running_loop().call_later(self.offline_grace + 0.1, self.notify)
        self.notify()

    def cancel_timers(self) -> None:
        """Called by stop(): nothing may fire after the panel is stopped."""
        if self._grace_handle:
            self._grace_handle.cancel()
            self._grace_handle = None

    @property
    def recently_connected(self) -> bool:
        """Connected, or only briefly disconnected (entities stay available)."""
        if self.connected:
            return True
        since = self.disconnected_since
        return since is not None and time.monotonic() - since < self.offline_grace and self.ever_connected

    @property
    def ever_connected(self) -> bool:
        return any(a.known for a in self.areas.values()) or any(z.known for z in self.zones.values())

    # ─── Mode helpers ───────────────────────────────────────────────────────

    @property
    def can_control(self) -> bool:
        """Whether this connection can arm and disarm (Crestron needs the UDL code)."""
        return True

    @property
    def offered_modes(self) -> list[str]:
        """Arm modes this panel can offer: Away always, Home/Night when mapped."""
        return ["away"] + [m for m in ("home", "night") if self.part_arms[m]]

    def mode_for_part_arm(self, part_arm: int | None) -> str | None:
        for mode in ("night", "home"):
            if part_arm and self.part_arms[mode] == part_arm:
                return mode
        return None

    def armed_state_for_part_arm(self, area: int, part_arm: int | None) -> str:
        """HA state for a part arm: its mapped mode, else the mode we asked
        for, else Home (a part arm nobody mapped is still a part arm)."""
        mode = self.mode_for_part_arm(part_arm)
        if mode is None:
            requested = self._requested_mode.get(area)
            mode = requested if requested in ("home", "night") else "home"
        return ARMED_STATE_FOR_MODE[mode]

    def set_area(self, number: int, state: str, part_arm: int | None = None, changed_by: str | None = None) -> None:
        area = self.areas.get(number)
        if area is None:
            return
        changed = not area.known or area.state != state or area.part_arm != part_arm
        if changed_by and changed_by != area.changed_by:
            area.changed_by = changed_by
            changed = True
        elif not changed_by and area.state != state and state in (DISARMED, ARMING, PENDING, TRIGGERED):
            # Nobody we know of did this (e.g. a fob disarm, or someone
            # walking in): don't carry "Home Assistant" or "User 3" over.
            area.changed_by = None
        area.state, area.part_arm, area.known = state, part_arm, True
        if state in (DISARMED, ARMED_AWAY, ARMED_HOME, ARMED_NIGHT):
            self._requested_mode.pop(number, None)
        if changed:
            _LOGGER.debug(
                "Area %s (%s) -> %s%s", number, area.name, state, f" (part arm {part_arm})" if part_arm else ""
            )
            self.notify()

    def set_zone(self, number: int, state: str, bypassed: bool | None = None) -> None:
        zone = self.zones.get(number)
        if zone is None:
            return
        new_bypassed = zone.bypassed if bypassed is None else bypassed
        if zone.known and zone.state == state and zone.bypassed == new_bypassed:
            return
        zone.state, zone.bypassed, zone.known = state, new_bypassed, True
        self.notify()

    # ─── Driver interface ───────────────────────────────────────────────────

    @abstractmethod
    async def start(self) -> None:
        """Connect in the background and keep reconnecting until stop()."""

    @abstractmethod
    async def stop(self) -> None:
        """Disconnect and stop reconnecting."""

    @abstractmethod
    async def arm(self, area: int, mode: str) -> None:
        """Arm an area in 'away', 'home' or 'night'. Raises PanelError."""

    @abstractmethod
    async def disarm(self, area: int) -> None:
        """Disarm an area (resetting it first if in alarm). Raises PanelError."""

    def diagnostics(self) -> dict[str, Any]:
        return {}
