"""What the panel's messages mean: zone, area and user events and the event
log. Alarms (and the zone that set one off), who armed or disarmed, failed
arms, part arms from the log, and engineer programming ending. Tamper and
fault log entries are passed on to conditions.py."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..panel import DISARMED, TRIGGERED, PanelArea, PanelZone, nice_name
from . import protocol as P
from .client import HostLog

# Log event types the driver reacts to (numbering as in texecom2mqtt).
LOG_AUTO_OPEN_CLOSE = 39
LOG_INSTALLER_PROGRAMMING_END = 59
LOG_ARM_FAILED = 85
PART_ARM_FROM_LOG = {78: 1, 79: 2, 80: 3, 204: 1, 205: 2, 206: 3, 207: 1, 208: 2, 209: 3}
GROUP_PRIORITY_ALARM_RESTORE = 2
GROUP_ALARM = 3
GROUP_TAMPER_ALARM = 11
GROUP_TAMPER_RESTORE = 12
FIRE_ALARM = 129
FIRE_ALARM_END = 130
USER_CHANGE_WINDOW = 60.0  # a keypad logon this recent explains an arm/disarm
ZONE_ALARM_REPEAT = 30.0


@dataclass
class Credit:
    """Who to name for the next arm or disarm: a keypad code (in any area,
    once in each), or a request from Home Assistant (in its own area, once)."""

    who: str
    at: float
    area: int | None = None  # Home Assistant's request: the area it was for
    used: set[int] = field(default_factory=set)  # areas whose arm or disarm it has explained


class EventsMixin:
    """Part of ConnectPanel: handles each message the panel sends."""

    # Attributes of the panel these methods use.
    zones: dict[int, PanelZone]
    areas: dict[int, PanelArea]
    on_event: Callable[[str, dict[str, Any]], None]
    _log: HostLog
    _last_part_arm: int | None
    _last_user: Credit | None
    _recent_alarms: dict[tuple[int, bool], float]
    _alarm_zones: dict[int, tuple[str, float]]  # area -> the zone that set its alarm off, and when
    _internal_alarms: dict[int, Any]
    _last_cause_at: float

    def _recent_user(self, area: int) -> str | None:
        credit = self._last_user
        if (
            credit
            and time.monotonic() - credit.at < USER_CHANGE_WINDOW
            and credit.area in (None, area)
            and area not in credit.used
        ):
            return credit.who
        return None

    def _on_message(self, m: dict[str, Any]) -> None:
        if m["kind"] == "log":
            self._log.debug("Connect: message %s: %s", m, P.describe_log(m))
        else:
            self._log.debug("Connect: message %s", m)
        kind = m["kind"]
        if kind == "zone":
            zs: P.ZoneState = m["state"]
            self.set_zone(m["zone"], zs.state, zs.bypassed)
            self._check_ready_soon(m["zone"])
            if zs.alarmed and zs.active:
                self._credit_alarm_zone(m["zone"])
        elif kind == "area":
            if m["state_code"] > 5:
                # Seen straight after "Part Armed 1" (settled part arm?): not
                # in the published lists, so re-read the flags instead.
                self._log.debug("Connect: area %s reported state %s; re-reading", m["area"], m["state_code"])
                self._refresh_areas_soon()
            elif m["state"] == "part armed":
                self._apply_area(m["area"], "part armed", self._last_part_arm, self._recent_user(m["area"]))
                self._refresh_areas_soon()  # confirm which part arm from the flags
            else:
                self._apply_area(m["area"], m["state"], None, self._recent_user(m["area"]))
            if m["state"] == "disarmed":
                self._refresh_areas_soon()  # whether it's ready to arm (not announced)
            self._read_display_soon()  # e.g. "Area in Entry"
            if m["state"] in ("disarmed", "armed", "part armed") and self._last_user:
                # A code (or a request from Home Assistant) explains one arm
                # or disarm in each area, not whatever happens next.
                self._last_user.used.add(m["area"])
        elif kind == "user":
            self._last_user = Credit(f"User {m['user']}", time.monotonic())
            self.on_event("user", {"user": m["user"], "method": m["method"]})
            if any(a.since is not None for a in self._internal_alarms.values()):
                self._refresh_areas_soon()  # a code silences the internal alarm (conditions.py)
        elif kind == "log":
            self._on_log(m)

    def _on_log(self, m: dict[str, Any]) -> None:
        if (
            m["type"] == LOG_ARM_FAILED
            or m["type"] in P.FAULT_LOG_NAMES
            or (m["type"] > 21 and m["group"] in (GROUP_TAMPER_ALARM, GROUP_TAMPER_RESTORE))
        ):
            self._read_display_soon()  # e.g. "Area arm fail", "AUX 0,0 Tamper"
        if m["type"] in PART_ARM_FROM_LOG:
            self._last_part_arm = PART_ARM_FROM_LOG[m["type"]]
        raw_group = m["group"] | (0x80 if m["communicated"] else 0) | (0x40 if m["comm_delayed"] else 0)
        if 1 <= m["type"] <= 21:
            # Zone alarm events carry the zone number in `parameter`.
            if m["group"] in (GROUP_ALARM, GROUP_TAMPER_ALARM) or raw_group == FIRE_ALARM:
                zone = self.zones.get(m["parameter"])
                tamper = m["group"] == GROUP_TAMPER_ALARM
                # The panel logs an alarm twice (seen on a real panel, the second
                # time once it has been reported): only the first one counts.
                key, now = (m["parameter"], tamper), time.monotonic()
                if self._recent_alarms.get(key, -ZONE_ALARM_REPEAT) > now - ZONE_ALARM_REPEAT:
                    return
                self._recent_alarms[key] = now
                self._last_cause_at = now
                self._credit_alarm_zone(m["parameter"])
                self._log.warning(
                    "Connect: zone %s (%s) in %s",
                    m["parameter"],
                    zone.name if zone else "unknown",
                    "tamper alarm" if tamper else "alarm",
                )
                self.on_event(
                    "zone_alarm", {"zone": m["parameter"], "zone_name": zone.name if zone else None, "tamper": tamper}
                )
            if m["group"] == GROUP_PRIORITY_ALARM_RESTORE or raw_group == FIRE_ALARM_END:
                self._refresh_areas_soon()
        if m["type"] > 21 and m["group"] in (GROUP_TAMPER_ALARM, GROUP_TAMPER_RESTORE):
            self._on_system_tamper(m)
        if m["type"] in P.FAULT_LOG_NAMES:
            self._on_fault(m)
        if m["type"] in (LOG_ARM_FAILED, LOG_AUTO_OPEN_CLOSE):
            if m["type"] == LOG_ARM_FAILED:
                # One log per zone that stopped the arm (seen on a real panel:
                # zones active at the end of the exit time).
                self._last_cause_at = time.monotonic()  # the panel's fail-to-set warning sounds
                zone = self.zones.get(m["parameter"])
                self._log.warning(
                    "Connect: arming failed: zone %s (%s) active", m["parameter"], zone.name if zone else "unknown"
                )
                self.on_event(
                    "arm_failed",
                    {"areas": m["areas"], "zone": m["parameter"], "zone_name": zone.name if zone else None},
                )
            self._refresh_areas_soon()  # these produce no area event
        elif m["type"] == LOG_INSTALLER_PROGRAMMING_END:
            self._log.info("Connect: engineer programming finished; re-reading zones and areas")
            self._spawn(self._rediscover())

    def _credit_alarm_zone(self, number: int) -> None:
        """Names the zone that set the alarm off as the alarm's "changed by",
        in each of the zone's areas (each area's alarm names its own zone).

        The panel flags it on the zone's state as the alarm starts (the log
        entry can come only after the disarm), in either order relative to
        the area's "in alarm" message.
        """
        zone = self.zones.get(number)
        if not zone:
            return
        name = nice_name(zone.name)
        now = time.monotonic()
        for area_number in zone.areas or list(self.areas):
            area = self.areas.get(area_number)
            # The alarmed bit stays set (alarm memory) after the disarm, so a
            # zone in a disarmed area doesn't explain a later alarm.
            if area is None or area.state == DISARMED:
                continue
            first = self._alarm_zones.get(area_number)
            if first and first[0] != name and now - first[1] < USER_CHANGE_WINDOW:
                continue  # the first zone in an alarm is the one that set it off
            self._alarm_zones[area_number] = (name, now)
            if area.state == TRIGGERED and area.changed_by != name and not self._alarm_named(area):
                area.changed_by = name
                self.notify()

    def _alarm_named(self, area: PanelArea) -> bool:
        """Whether an area in alarm already names the zone that set it off.
        It stays named for as long as the alarm lasts, however long: a zone
        seen later (someone walking in while the sirens sound) didn't."""
        return area.state == TRIGGERED and area.changed_by in {nice_name(z.name) for z in self.zones.values()}
