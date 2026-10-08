"""Conditions the panel reports besides zones and areas: tampers that aren't
zones (the panel lid, the shared detector tamper circuit...), faults from the
event log, and mains power worked out from the power readings. They show in
panel.extra["tampers"] and panel.extra["faults"]."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..panel import PanelZone, nice_name
from . import protocol as P
from .client import HostLog
from .events import GROUP_TAMPER_ALARM

MAINS_VOLTAGE = 13.3  # below this with no current flowing, the panel is on battery
BATTERY_ONLY_VOLTAGE = 12.9  # the same, before the panel has ever reported current (mains gives ~13.6 V)


class ConditionsMixin:
    """Part of ConnectPanel: tamper, fault and mains handling."""

    # Attributes of the panel these methods use.
    zones: dict[int, PanelZone]
    extra: dict[str, Any]
    on_event: Callable[[str, dict[str, Any]], None]
    _log: HostLog
    _seen_current: bool

    def _on_system_tamper(self, m: dict[str, Any]) -> None:
        """Tampers that aren't zones, e.g. the panel lid (type 60) or a
        detector on the shared auxiliary tamper circuit (type 62), seen on a
        real panel: group 11 when opened, 12 when closed again."""
        active = m["group"] == GROUP_TAMPER_ALARM
        name = P.TAMPER_LOG_NAMES.get(m["type"], f"Tamper (log type {m['type']})")
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
