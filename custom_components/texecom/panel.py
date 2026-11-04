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
ARMED_VACATION = "armed_vacation"
ARMED_CUSTOM_BYPASS = "armed_custom_bypass"
TRIGGERED = "triggered"

# Away is the full arm; each of the others can use one of the panel's part arms.
PART_ARM_MODES = ("home", "night", "vacation", "custom_bypass")
ARM_MODES = ("away", *PART_ARM_MODES)
ARMED_STATE_FOR_MODE = {
    "away": ARMED_AWAY,
    "home": ARMED_HOME,
    "night": ARMED_NIGHT,
    "vacation": ARMED_VACATION,
    "custom_bypass": ARMED_CUSTOM_BYPASS,
}
ARMED_STATES = frozenset(ARMED_STATE_FOR_MODE.values())
# A part arm no mode uses shows as the first of these that no part arm uses,
# so it never looks like one that has a button (issue #32), and Apple Home
# still shows it as armed (HomeKit has no custom bypass state). The area's
# part_arm says which it was.
UNMAPPED_PART_ARM_MODES = ("home", "night", "vacation")

# How long entities keep showing the last known state after the connection
# drops. A SmartCom drops the session for about a minute whenever it reports
# an alarm (seen on a real panel), which is exactly when the alarm should stay
# visible; the panel connection sensor shows the real link state throughout.
OFFLINE_GRACE = 180.0

# Fault names (in extra["faults"]) that mean the panel has no mains power.
MAINS_FAULTS = {"AC Fail", "PSU AC Fail"}


# Kept in capitals when a panel name is title-cased ("HALL PIR" -> "Hall PIR").
ACRONYMS = {"PIR", "PA", "CO", "CO2", "GSM", "UPS", "LED", "AC", "DC", "CCTV", "WC", "PSU", "UDL", "RF", "IR", "ATS"}


def nice_name(name: str) -> str:
    """Panels store names in capitals ("HOUSE"); show them in title case."""
    if not name.isupper():
        return name
    return " ".join(word if word in ACRONYMS else word.title() for word in name.split(" "))


class PanelError(Exception):
    """An arm/disarm request failed or the panel refused it."""


class PanelNotConnected(PanelError):
    """There's no connection to the panel just now (it reconnects by itself)."""


class PanelRefused(PanelError):
    """The panel answered, but refused the request."""


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
    ready: bool | None = None  # ready to arm, as the panel says (None: it doesn't say)
    arming_to: str | None = None  # while arming, the armed state it'll be (e.g. ARMED_NIGHT), when known


@dataclass
class PanelInfo:
    model: str = "Premier Elite"
    zones: int | None = None
    firmware: str | None = None


class TexecomPanel(ABC):
    """A panel connection that keeps zones and areas up to date.

    part_arms maps the Home, Night, Vacation and Custom bypass modes to a panel
    part arm (1-3), or 0 when the mode isn't offered (a missing mode is 0). Listeners are called with no arguments after
    any change (zone, area, connection or info).
    """

    def __init__(self, part_arms: dict[str, int]) -> None:
        self.part_arms = {mode: int(part_arms.get(mode) or 0) for mode in PART_ARM_MODES}
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
        # Where the next read of the panel's stored event log starts; None
        # until the first reading, which then only notes where the log ends.
        self._log_pointer: int | None = None

    # ─── The stored event log ───────────────────────────────────────────────

    @property
    def log_pointer(self) -> int | None:
        """Where the next read of the stored event log starts (None until the
        first reading)."""
        return self._log_pointer

    def resume_log_from(self, pointer: int | None) -> None:
        """Before start(): carry on reading the stored log from where the last
        run left it, so entries logged in between (a reload, a restart) are
        read rather than skipped."""
        self._log_pointer = pointer

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

    # Whether the panel says when an area is ready to arm (PanelArea.ready).
    reports_ready = False

    def set_area_ready(self, number: int, ready: bool) -> None:
        area = self.areas.get(number)
        if area is not None and area.ready != ready:
            area.ready = ready
            self.notify()

    # ─── Mode helpers ───────────────────────────────────────────────────────

    @property
    def can_control(self) -> bool:
        """Whether this connection can arm and disarm (Crestron needs the UDL code)."""
        return True

    @property
    def offered_modes(self) -> list[str]:
        """Arm modes this panel can offer: Away always, the others when mapped."""
        return ["away"] + [m for m in PART_ARM_MODES if self.part_arms[m]]

    def mode_for_part_arm(self, part_arm: int | None) -> str | None:
        for mode in PART_ARM_MODES:
            if part_arm and self.part_arms[mode] == part_arm:
                return mode
        return None

    def armed_state_for_part_arm(self, area: int, part_arm: int | None) -> str:
        """HA state for a part arm: its mapped mode, else the mode we asked
        for, else a free mode (UNMAPPED_PART_ARM_MODES)."""
        mode = self.mode_for_part_arm(part_arm)
        if mode is None:
            requested = self._requested_mode.get(area)
            free = next((m for m in UNMAPPED_PART_ARM_MODES if not self.part_arms[m]), "home")
            mode = requested if requested in PART_ARM_MODES else free
        return ARMED_STATE_FOR_MODE[mode]

    def set_area(self, number: int, state: str, part_arm: int | None = None, changed_by: str | None = None) -> None:
        area = self.areas.get(number)
        if area is None:
            return
        changed = not area.known or area.state != state or area.part_arm != part_arm
        if changed_by and changed_by != area.changed_by:
            area.changed_by = changed_by
            changed = True
        elif (
            not changed_by
            and area.state != state
            and (state in (DISARMED, ARMING, PENDING, TRIGGERED) or area.state in (DISARMED, TRIGGERED))
        ):
            # Nobody we know of did this (e.g. a fob disarm, someone walking
            # in, or an arm with no exit time): don't carry "Home Assistant"
            # or "User 3" over. Armed after an exit delay keeps who started it.
            area.changed_by = None
        area.state, area.part_arm, area.known = state, part_arm, True
        if state == DISARMED or state in ARMED_STATES:
            self._requested_mode.pop(number, None)
        if state != ARMING:
            area.arming_to = None
        elif area.arming_to is None and (mode := self._requested_mode.get(number)):
            area.arming_to = ARMED_STATE_FOR_MODE[mode]  # Home Assistant asked for it
        if changed:
            _LOGGER.debug(
                "Area %s (%s) -> %s%s", number, area.name, state, f" (part arm {part_arm})" if part_arm else ""
            )
            self.notify()

    def set_area_arming_to(self, number: int, armed_state: str) -> None:
        """While an area is arming, the armed state it'll be, once the panel says."""
        area = self.areas.get(number)
        if area is None or area.state != ARMING or area.arming_to == armed_state:
            return
        area.arming_to = armed_state
        _LOGGER.debug("Area %s (%s) arming to %s", number, area.name, armed_state)
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
        """Arm an area in one of ARM_MODES. Raises PanelError."""

    @abstractmethod
    async def disarm(self, area: int) -> None:
        """Disarm an area (resetting it first if in alarm). Raises PanelError."""

    def diagnostics(self) -> dict[str, Any]:
        return {}
