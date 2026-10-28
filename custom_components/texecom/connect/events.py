"""What the panel's messages mean: zone, area and user events and the event
log. Alarms (and the zone that set one off), who armed or disarmed, failed
arms, part arms from the log, and engineer programming ending. Tamper and
fault log entries are passed on to conditions.py. After a reconnect, what
the stored event log holds from while the session was down."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..panel import DISARMED, TRIGGERED, PanelArea, PanelInfo, PanelZone, nice_name
from . import protocol as P
from .capture import Capture
from .client import ConnectClient, ConnectError, HostLog

# Log event types the driver reacts to (numbering as in texecom2mqtt).
LOG_AUTO_OPEN_CLOSE = 39
LOG_INSTALLER_PROGRAMMING_END = 59
LOG_DOWNLOAD_END = 54  # a Wintex (UDL) session ended: it may have changed zones or areas
# Settings written (by Wintex): sent at once, one per write, whether or not the session then ends
# cleanly (through a serial adapter on a Crestron port, Download End never came).
LOG_SITE_DATA_CHANGED = 100
REDISCOVER_DELAY = 5.0  # seconds after the last Site Data Changed before reading the zones again
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
# Flag re-reads (about 0.5 s apart) in an exit delay until they say what it's
# arming to: a full arm's flag came about 1.5 s after the exit delay started.
EXIT_READS = 4
# After a reconnect, the stored log entries written since the last login are
# read (at most this many, 5 per read), and those that didn't come live are
# applied: a tamper closed or a fault ended while the session was down (e.g.
# the ~2 minutes a SmartCom takes to report an alarm) doesn't stay on.
MAX_LOG_CATCH_UP = 30
LIVE_LOG_KEPT = 128  # live log messages remembered, so the catch-up can skip them


def log_key(m: dict[str, Any]) -> tuple:
    """What identifies a log entry, live or stored: they match to the second
    (the communicated bits can differ)."""
    return m["type"], m["group"], m["parameter"], m["areas"], tuple(sorted(m["time"].items()))


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
    capture: Capture
    _last_part_arm: int | None
    _last_user: Credit | None
    _recent_alarms: dict[tuple[int, bool], float]
    _alarm_zones: dict[int, tuple[str, float]]  # area -> the zone that set its alarm off, and when
    _internal_alarms: dict[int, Any]
    _last_cause_at: float
    _rediscover_handle: asyncio.TimerHandle | None
    _rediscover_pending: bool
    _exit_reads: dict[int, int]
    client: ConnectClient | None
    info: PanelInfo
    _log_pointer: int | None  # where the stored log ended at the last login
    _live_log: deque[tuple]  # log_key of the latest live log messages
    _log_unanswered: bool  # the panel didn't answer for its log: don't ask again (each try costs ~7 s)

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
            if m["state"].startswith("unknown"):
                self._log.debug("Connect: area %s reported state %s; re-reading", m["area"], m["state_code"])
                self._refresh_areas_soon()
            elif "part_arm" in m:  # states 6-8 say which part arm
                self._apply_area(m["area"], "part armed", m["part_arm"], self._recent_user(m["area"]))
            elif m["state"] == "part armed":
                self._apply_area(m["area"], "part armed", self._last_part_arm, self._recent_user(m["area"]))
                self._refresh_areas_soon()  # confirm which part arm from the flags
            else:
                self._apply_area(m["area"], m["state"], None, self._recent_user(m["area"]))
            if m["state"] == "disarmed":
                self._refresh_areas_soon()  # whether it's ready to arm (not announced)
            elif m["state"] == "in exit":
                self._exit_reads[m["area"]] = EXIT_READS
                self._refresh_areas_soon()  # what it's arming to: only the flags say
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
            self._live_log.append(log_key(m))
            self._on_log(m)
        self.capture.add(m)  # afterwards: recording it can never stop it being handled

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
                self._log.info(
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
                self._log.info(
                    "Connect: arming failed: zone %s (%s) active", m["parameter"], zone.name if zone else "unknown"
                )
                self.on_event(
                    "arm_failed",
                    {"areas": m["areas"], "zone": m["parameter"], "zone_name": zone.name if zone else None},
                )
            self._refresh_areas_soon()  # these produce no area event
        elif m["type"] in (LOG_INSTALLER_PROGRAMMING_END, LOG_DOWNLOAD_END):
            how = "engineer programming" if m["type"] == LOG_INSTALLER_PROGRAMMING_END else "a Wintex session"
            self._log.info("Connect: %s finished; re-reading zones and areas", how)
            if self._rediscover_handle:  # this read covers the settings it wrote too
                self._rediscover_handle.cancel()
                self._rediscover_handle = None
            self._spawn(self._rediscover())
        elif m["type"] == LOG_SITE_DATA_CHANGED:
            self._rediscover_soon()

    async def _catch_up_log(self) -> None:
        """After the login: the stored log entries written since the last
        login that didn't come live. The first login only notes where the
        log ends (its history isn't news)."""
        size, capacity = P.log_entry_size(self.info.zones), P.LOG_CAPACITY.get(self.info.zones)
        client = self.client
        if not client or not size or not capacity or self._log_unanswered:
            return
        try:
            pointer = await client.log_pointer()
        except ConnectError:
            if not client.connected:
                raise
            self._log.debug("Connect: no answer for the event log's position; not asking again")
            self._log_unanswered = True
            return
        if pointer is None or not 0 <= pointer < capacity:
            self._log.debug("Connect: the event log's position was refused; no catching up this time")
            return
        start, self._log_pointer = self._log_pointer, pointer
        if start is None:
            return
        count = (pointer - start) % capacity
        if count > MAX_LOG_CATCH_UP:
            start, count = (pointer - MAX_LOG_CATCH_UP) % capacity, MAX_LOG_CATCH_UP
        missed: list[dict[str, Any]] = []
        while count:
            chunk = min(count, P.MAX_LOG_EVENTS_PER_READ, capacity - start)  # a read never runs past the ring's end
            try:
                entries = await client.log_entries(start, chunk, size)
            except ConnectError:
                if not client.connected:
                    raise
                self._log_unanswered, entries = True, None
            if entries is None:
                self._log.debug("Connect: event log read at %d failed; catching up stops there", start)
                break
            missed += [m for m in entries if log_key(m) not in self._live_log]
            start, count = (start + chunk) % capacity, count - chunk
        for m in missed:
            self._log.debug("Connect: logged while disconnected: %s", P.describe_log(m))
            self._on_missed_log(m)

    def _on_missed_log(self, m: dict[str, Any]) -> None:
        """A log entry written while disconnected. Only what's still true now
        is applied: a tamper or fault starting or ending, settings changed.
        Not momentary events: a zone alarm or a failed arm reported late
        would look like a new one (and a zone alarm is logged again once it
        has been reported), and the area states read at the login already
        show where things are."""
        if m["type"] > 21 and m["group"] in (GROUP_TAMPER_ALARM, GROUP_TAMPER_RESTORE):
            self._on_system_tamper(m)
        if m["type"] in P.FAULT_LOG_NAMES:
            self._on_fault(m)
        if m["type"] in (LOG_INSTALLER_PROGRAMMING_END, LOG_DOWNLOAD_END, LOG_SITE_DATA_CHANGED):
            self._rediscover_pending = True  # read once, after the login (panel.py)

    def _rediscover_soon(self) -> None:
        """Reads the zones and areas again once the writes have stopped."""
        if self._rediscover_handle:
            self._rediscover_handle.cancel()

        def run() -> None:
            self._rediscover_handle = None
            self._log.info("Connect: the panel's settings changed; re-reading zones and areas")
            self._spawn(self._rediscover())

        self._rediscover_handle = asyncio.get_running_loop().call_later(REDISCOVER_DELAY, run)

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
