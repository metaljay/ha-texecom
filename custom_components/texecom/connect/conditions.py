"""Conditions the panel reports besides zones and areas: tampers that aren't
zones (the panel lid, the shared detector tamper circuit...), the internal
alarm going off with nothing reported to explain it, faults from the event
log, and mains power worked out from the power readings. They show in
panel.extra["tampers"] and panel.extra["faults"]."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..panel import DISARMED, PanelArea, PanelZone, nice_name
from . import protocol as P
from .client import ConnectClient, HostLog
from .events import GROUP_TAMPER_ALARM

MAINS_VOLTAGE = 13.3  # below this with no current flowing, the panel is on battery
BATTERY_ONLY_VOLTAGE = 12.9  # the same, before the panel has ever reported current (mains gives ~13.6 V)

# The tamper shown while the panel's internal alarm (area flag 44) is on with
# nothing reported to explain it. Seen on a real panel: a detector's cover
# opened, the internal sounder went off, but the tamper wasn't logged.
INTERNAL_ALARM = "Internal Alarm"
INTERNAL_ALARM_CONFIRM = 5.0  # seconds the flag must stay set before it counts
CAUSE_MARGIN = 2.0  # a cause reported this long before the flag was last read clear still explains it


@dataclass
class InternalAlarm:
    """Area flag 44 for one area: when it was last read clear, and since when
    it has been set (until it's read clear again)."""

    clear_at: float | None = None
    since: float | None = None
    explained: bool = False  # something reported explains it
    shown: bool = False  # it shows as a tamper


class ConditionsMixin:
    """Part of ConnectPanel: tamper, fault and mains handling."""

    # Attributes of the panel these methods use.
    client: ConnectClient | None
    zones: dict[int, PanelZone]
    areas: dict[int, PanelArea]
    extra: dict[str, Any]
    on_event: Callable[[str, dict[str, Any]], None]
    panel_zones: int
    _log: HostLog
    _seen_current: bool
    _internal_alarms: dict[int, InternalAlarm]
    _internal_alarm_handle: asyncio.TimerHandle | None
    _last_cause_at: float  # when the panel last reported a tamper, a zone alarm or a failed arm

    def _on_system_tamper(self, m: dict[str, Any]) -> None:
        """Tampers that aren't zones, e.g. the panel lid (type 60) or a
        detector on the shared auxiliary tamper circuit (type 62), seen on a
        real panel: group 11 when opened, 12 when closed again."""
        active = m["group"] == GROUP_TAMPER_ALARM
        name = P.TAMPER_LOG_NAMES.get(m["type"], f"Tamper (log type {m['type']})")
        if active:
            self._last_cause_at = time.monotonic()
        tampers: set[str] = self.extra.setdefault("tampers", set())
        if active == (name in tampers):
            return  # the panel logs some events twice (again once reported)
        (tampers.add if active else tampers.discard)(name)
        self.notify()
        if active:
            self._log.warning("Connect: %s", name)
            self.on_event("tamper", {"source": name, "log_type": m["type"]})
        else:
            self._log.info("Connect: %s cleared", name)
            self.on_event("tamper_cleared", {"source": name, "log_type": m["type"]})

    def _check_internal_alarm(self) -> None:
        """Called after each area read. The panel's internal alarm (area flag
        44) on while an area is disarmed, with nothing reported to explain
        it, shows as the tamper INTERNAL_ALARM until the flag clears (a code
        at the keypad clears it).

        Explained, so not shown: the flag during an exit or entry delay, an
        alarm or an arm (the area isn't disarmed at some point while it's
        set); after a tamper, zone alarm or failed arm the panel reported
        (since the flag was last read clear); while another tamper is open.
        A flag set for less than INTERNAL_ALARM_CONFIRM seconds isn't shown."""
        flags = self.client.last_area_flags if self.client else None
        if not flags:
            return
        now = time.monotonic()
        other_tampers = self.extra.get("tampers", set()) - {INTERNAL_ALARM}
        for number, area in self.areas.items():
            if P.FLAG_INTERNAL_ALARM not in P.area_flags_set(flags, number, self.panel_zones):
                self._internal_alarms[number] = InternalAlarm(clear_at=now)
                continue
            alarm = self._internal_alarms.setdefault(number, InternalAlarm())
            if alarm.since is None:
                alarm.since = now
            if alarm.shown or alarm.explained:
                continue
            began = alarm.clear_at if alarm.clear_at is not None else alarm.since
            alarm.explained = (
                area.state != DISARMED or bool(other_tampers) or self._last_cause_at >= began - CAUSE_MARGIN
            )
            if alarm.explained:
                continue
            if now - alarm.since >= INTERNAL_ALARM_CONFIRM:
                alarm.shown = True
            else:
                self._confirm_internal_alarm_soon()
        self._show_internal_alarm(any(a.shown for a in self._internal_alarms.values()))

    def _confirm_internal_alarm_soon(self) -> None:
        """Reads the flags again once the flag has been set long enough to count."""
        if self._internal_alarm_handle:
            return

        def run() -> None:
            self._internal_alarm_handle = None
            self._spawn(self._quiet(self.refresh_areas()))

        self._internal_alarm_handle = asyncio.get_running_loop().call_later(INTERNAL_ALARM_CONFIRM + 0.1, run)

    def _show_internal_alarm(self, shown: bool) -> None:
        if shown == (INTERNAL_ALARM in self.extra.get("tampers", ())):
            return
        tampers: set[str] = self.extra.setdefault("tampers", set())
        (tampers.add if shown else tampers.discard)(INTERNAL_ALARM)
        self.notify()
        if shown:
            self._log.warning("Connect: the panel's internal alarm is on, with no tamper or alarm reported")
            self.on_event("tamper", {"source": INTERNAL_ALARM, "log_type": None})
        else:
            self._log.info("Connect: the panel's internal alarm is off again")
            self.on_event("tamper_cleared", {"source": INTERNAL_ALARM, "log_type": None})

    def _mains_from_power(self, power: P.SystemPower) -> None:
        """Mains on or off from the power readings. Seen on a real panel: on
        battery both currents read 0 and the voltage falls below 13 V; with
        mains, ~300 mA flows at ~13.6 V. The panel logs the mains failing at
        once but didn't log it coming back, so this clears (or confirms) it,
        and gives the right answer after a restart."""
        if power.panel_current > 0:
            self._seen_current = True
        # Only trust "no current" from a panel that has reported current
        # before; otherwise (a panel that always reads 0 mA, or a restart
        # while on battery) only a clearly low voltage counts.
        threshold = MAINS_VOLTAGE if self._seen_current else BATTERY_ONLY_VOLTAGE
        on_battery = power.panel_current == 0 and power.panel_voltage < threshold
        mains_ok = power.panel_current > 0 or power.panel_voltage >= MAINS_VOLTAGE
        faults: set[str] = self.extra.setdefault("faults", set())
        if on_battery and "AC Fail" not in faults:
            faults.add("AC Fail")
            self._log.warning("Connect: running on battery (no mains current, %.2f V)", power.panel_voltage)
            self.on_event("fault", {"source": "AC Fail", "log_type": None})
        elif mains_ok and "AC Fail" in faults:
            # Only the panel's own mains: a remote power supply's (PSU AC
            # Fail) isn't in these readings, so it waits for its own restore.
            faults.discard("AC Fail")
            self._log.info("Connect: mains back (%d mA, %.2f V)", power.panel_current, power.panel_voltage)
            self.on_event("fault_cleared", {"source": "AC Fail", "log_type": None})

    def _on_fault(self, m: dict[str, Any]) -> None:
        """Mains, battery, communication and other faults from the panel log."""
        name = P.FAULT_LOG_NAMES[m["type"]]
        if m["type"] in P.ZONE_FAULT_LOGS and (zone := self.zones.get(m["parameter"])):
            name = f"{name}: {nice_name(zone.name)}"
        if m["group"] in P.GROUPS_STARTING:
            active = True
        elif m["group"] in P.GROUPS_RESTORING:
            active = False
        else:
            self._log.debug("Connect: %s with group %s (not a start or end)", name, m["group"])
            return
        faults: set[str] = self.extra.setdefault("faults", set())
        if active == (name in faults):
            return  # already known (logged twice, or seen in the power readings)
        (faults.add if active else faults.discard)(name)
        self.notify()
        self._log.warning("Connect: %s%s", name, "" if active else " cleared")
        self.on_event("fault" if active else "fault_cleared", {"source": name, "log_type": m["type"]})
