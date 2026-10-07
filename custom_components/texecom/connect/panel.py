"""Texecom Connect panel driver: discovery, state sync and arm/disarm."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from datetime import datetime, tzinfo
from typing import Any

from ..panel import (
    ARMED_AWAY,
    ARMING,
    DISARMED,
    PENDING,
    TRIGGERED,
    PanelArea,
    PanelError,
    PanelInfo,
    PanelZone,
    TexecomPanel,
)
from . import protocol as P
from .client import ConnectClient, ConnectError, LoginRejected, PanelBusyError, Timing

_LOGGER = logging.getLogger(__name__)

RECONNECT_MIN = 5.0
RECONNECT_MAX = 60.0
POWER_EVERY_N_IDLE = 10  # read voltages every ~5 minutes

# Log event types the driver reacts to (numbering as in texecom2mqtt).
LOG_AUTO_OPEN_CLOSE = 39
LOG_INSTALLER_PROGRAMMING_END = 59
LOG_ARM_FAILED = 85
PART_ARM_FROM_LOG = {78: 1, 79: 2, 80: 3, 204: 1, 205: 2, 206: 3, 207: 1, 208: 2, 209: 3}
GROUP_PRIORITY_ALARM_RESTORE = 2
GROUP_ALARM = 3
GROUP_TAMPER_ALARM = 11
FIRE_ALARM = 129
FIRE_ALARM_END = 130
USER_CHANGE_WINDOW = 60.0  # a keypad logon this recent explains an arm/disarm
ZONE_ALARM_REPEAT = 30.0


async def discover(client: ConnectClient) -> tuple[PanelInfo, list[PanelZone], list[PanelArea]]:
    """Reads panel identity, zones in use (name, type, areas) and the areas
    that contain them (like texecom2mqtt, empty areas are left out)."""
    ident = await client.panel_identification()
    if not ident.zones:
        raise ConnectError(f"unrecognised panel identification {ident.text!r}")
    info = PanelInfo(model=ident.model, zones=ident.zones, firmware=ident.firmware)
    zones: list[PanelZone] = []
    for number in range(1, ident.zones + 1):
        details = await client.zone_details(number)
        if not details or details.type == 0:
            continue  # not used
        zones.append(PanelZone(number, details.name or f"Zone {number}", details.type, details.areas))
    areas: list[PanelArea] = []
    for number in sorted({a for z in zones for a in z.areas}):
        details = await client.area_details(number)
        areas.append(PanelArea(number, (details and details.name) or f"Area {chr(64 + number)}"))
    return info, zones, areas


async def probe(host: str, port: int, udl: str) -> tuple[PanelInfo, list[PanelZone], list[PanelArea]]:
    """One-off login and discovery (used when setting up)."""
    client = ConnectClient(host, port, udl)
    await client.connect()
    try:
        return await discover(client)
    finally:
        await client.close()


class ConnectPanel(TexecomPanel):
    """Keeps one Connect session open, reconnecting with back-off."""

    def __init__(
        self,
        host: str,
        port: int,
        udl: str,
        part_arms: dict[str, int],
        info: PanelInfo,
        zones: list[PanelZone],
        areas: list[PanelArea],
        time_zone: tzinfo | None = None,
        time_sync_hours: float = 0,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
        on_layout_changed: Callable[[PanelInfo, list[PanelZone], list[PanelArea]], None] | None = None,
        on_auth_failed: Callable[[], None] | None = None,
        timing: Timing | None = None,
        reconnect_min: float = RECONNECT_MIN,
    ) -> None:
        super().__init__(part_arms)
        self.host, self.port, self.udl = host, port, str(udl)
        self.info = info
        self.zones = {z.number: z for z in zones}
        self.areas = {a.number: a for a in areas}
        self.time_zone = time_zone
        self.time_sync_seconds = max(0.0, min(float(time_sync_hours or 0), 744.0)) * 3600
        self.on_event = on_event or (lambda _t, _d: None)
        self.on_layout_changed = on_layout_changed
        self.on_auth_failed = on_auth_failed
        self.timing = timing
        self.reconnect_min = reconnect_min
        self.client: ConnectClient | None = None
        self._task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self._stopped = False
        self._closed_event = asyncio.Event()
        self._last_part_arm: int | None = None
        self._last_user: tuple[str, float] | None = None
        self._recent_alarms: dict[tuple[int, bool], float] = {}
        self._idle_count = 0
        self._refresh_handle: asyncio.TimerHandle | None = None
        self.last_error: str | None = None

    @property
    def panel_zones(self) -> int:
        return self.info.zones or 24

    # ─── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._stopped = False
        self._task = asyncio.get_running_loop().create_task(self._run(), name="texecom connect")

    async def stop(self) -> None:
        self._stopped = True
        if self._refresh_handle:
            self._refresh_handle.cancel()
        for task in [self._task, *self._tasks]:
            if task and not task.done():
                task.cancel()
        if self.client:
            await self.client.close()
        for task in [self._task, *self._tasks]:
            if task:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self.set_connected(False)

    def _spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self) -> None:
        delay = self.reconnect_min
        while not self._stopped:
            self._closed_event.clear()
            client = ConnectClient(
                self.host,
                self.port,
                self.udl,
                on_message=self._on_message,
                on_close=lambda _reason: self._closed_event.set(),
                on_idle=self._on_idle,
                timing=self.timing,
            )
            self.client = client
            try:
                _LOGGER.debug("Connect: connecting to %s:%s", self.host, self.port)
                await client.connect()
                await self._start_up()
                _LOGGER.info("Connect: logged in to %s:%s", self.host, self.port)
                self.last_error = None
                delay = self.reconnect_min
                self.set_connected(True)
                sync_task = None
                if self.time_sync_seconds > 0:
                    sync_task = asyncio.get_running_loop().create_task(self._time_sync_loop())
                try:
                    await self._closed_event.wait()
                finally:
                    if sync_task:
                        sync_task.cancel()
            except asyncio.CancelledError:
                raise
            except LoginRejected as err:
                self.last_error = str(err)
                _LOGGER.error("Connect: %s", err)
                if self.on_auth_failed:
                    self.on_auth_failed()
                    self._stopped = True  # wait for a new code rather than retry
            except Exception as err:  # noqa: BLE001 - any failure means reconnect
                self.last_error = str(err)
                _LOGGER.warning("Connect: %s", err)
            finally:
                await client.close()
                self.set_connected(False)
            if self._stopped:
                break
            _LOGGER.info("Connect: reconnecting in %.0f s", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, max(RECONNECT_MAX, self.reconnect_min))

    async def _start_up(self) -> None:
        for attempt in range(3):
            try:
                await self.refresh()
                await self.read_power()
                return
            except PanelBusyError:
                if attempt == 2:
                    raise
                await asyncio.sleep(1)

    # ─── State ──────────────────────────────────────────────────────────────

    async def refresh(self) -> None:
        """Reads the state of every zone and area (also the keep-alive)."""
        assert self.client
        states = await self.client.zone_states(self.panel_zones)
        for number, zs in states.items():
            self.set_zone(number, zs.state, zs.bypassed)
        await self.refresh_areas()

    async def refresh_areas(self) -> None:
        if not self.areas or not self.client:
            return
        states = await self.client.area_states(list(self.areas), self.panel_zones)
        for number, (state, part_arm) in states.items():
            self._apply_area(number, state, part_arm)

    async def _on_idle(self) -> None:
        self._idle_count += 1
        await self.refresh()
        if self._idle_count % POWER_EVERY_N_IDLE == 1:
            await self.read_power()

    async def read_power(self) -> None:
        assert self.client
        with contextlib.suppress(PanelBusyError):
            power = await self.client.system_power()
            if power:
                self.extra["power"] = power
                self.notify()

    def _apply_area(self, number: int, state: str, part_arm: int | None, changed_by: str | None = None) -> None:
        area = self.areas.get(number)
        if area is None:
            return
        if state == "disarmed":
            self.set_area(number, DISARMED, None, changed_by)
        elif state == "in exit":
            # An armed area can't start an exit delay without being disarmed
            # first; a flag re-read just after a remote arm can still show the
            # exit flag (seen on a real panel), so ignore it.
            if area.known and area.state not in (DISARMED, TRIGGERED, ARMING):
                _LOGGER.debug("Connect: area %s: ignoring a stale exit flag while armed", number)
                return
            self.set_area(number, ARMING, None, changed_by)
        elif state == "in entry":
            self.set_area(number, PENDING, area.part_arm, changed_by)
        elif state == "armed":
            self.set_area(number, ARMED_AWAY, None, changed_by)
        elif state == "part armed":
            self.set_area(number, self.armed_state_for_part_arm(number, part_arm), part_arm, changed_by)
        elif state == "in alarm":
            self.set_area(number, TRIGGERED, area.part_arm, changed_by)

    def _recent_user(self) -> str | None:
        if self._last_user and time.monotonic() - self._last_user[1] < USER_CHANGE_WINDOW:
            return self._last_user[0]
        return None

    def _on_message(self, m: dict[str, Any]) -> None:
        kind = m["kind"]
        if kind == "zone":
            zs: P.ZoneState = m["state"]
            self.set_zone(m["zone"], zs.state, zs.bypassed)
        elif kind == "area":
            if m["state_code"] > 5:
                # Seen straight after "Part Armed 1" (settled part arm?): not
                # in the published lists, so re-read the flags instead.
                _LOGGER.debug("Connect: area %s reported state %s; re-reading", m["area"], m["state_code"])
                self._refresh_areas_soon()
            elif m["state"] == "part armed":
                self._apply_area(m["area"], "part armed", self._last_part_arm, self._recent_user())
                self._refresh_areas_soon()  # confirm which part arm from the flags
            else:
                self._apply_area(m["area"], m["state"], None, self._recent_user())
        elif kind == "user":
            self._last_user = (f"User {m['user']}", time.monotonic())
            self.on_event("user", {"user": m["user"], "method": m["method"]})
        elif kind == "log":
            self._on_log(m)

    def _on_log(self, m: dict[str, Any]) -> None:
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
                if zone:  # the area's "changed by" names the zone that set it off
                    self._last_user = (zone.name.title() if zone.name.isupper() else zone.name, now)
                _LOGGER.warning(
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
        if m["type"] in (LOG_ARM_FAILED, LOG_AUTO_OPEN_CLOSE):
            if m["type"] == LOG_ARM_FAILED:
                self.on_event("arm_failed", {"areas": m["areas"]})
            self._refresh_areas_soon()  # these produce no area event
        elif m["type"] == LOG_INSTALLER_PROGRAMMING_END:
            _LOGGER.info("Connect: engineer programming finished; re-reading zones and areas")
            self._spawn(self._rediscover())

    def _refresh_areas_soon(self) -> None:
        if self._refresh_handle:
            self._refresh_handle.cancel()

        def run() -> None:
            self._refresh_handle = None
            self._spawn(self._quiet(self.refresh_areas()))

        self._refresh_handle = asyncio.get_running_loop().call_later(0.5, run)

    async def _quiet(self, coro) -> None:
        try:
            await coro
        except ConnectError as err:
            _LOGGER.debug("Connect: area refresh skipped: %s", err)

    async def _rediscover(self) -> None:
        if not self.client:
            return
        try:
            info, zones, areas = await discover(self.client)
        except ConnectError as err:
            _LOGGER.warning("Connect: re-reading zones failed: %s", err)
            return

        def layout(zs, ars):
            return ([(z.number, z.name, z.panel_type, z.areas) for z in zs], [(a.number, a.name) for a in ars])

        if layout(zones, areas) != layout(self.zones.values(), self.areas.values()) and self.on_layout_changed:
            self.on_layout_changed(info, zones, areas)

    # ─── Clock ──────────────────────────────────────────────────────────────

    async def _time_sync_loop(self) -> None:
        await asyncio.sleep(5)  # soon after connecting, e.g. after a power cut
        while True:
            try:
                await self.sync_clock()
            except ConnectError as err:
                _LOGGER.debug("Connect: clock check failed: %s", err)
            await asyncio.sleep(self.time_sync_seconds)

    async def sync_clock(self, now: datetime | None = None) -> bool:
        """Sets the panel clock if it is more than a minute out.

        The panel keeps local wall-clock time; Home Assistant's own time zone
        is used, so a Docker container running on UTC can't put the panel an
        hour out (a bug found in the Homebridge plugin).
        """
        assert self.client
        panel = await self.client.date_time()
        if not panel:
            return False
        now = now or datetime.now(self.time_zone)
        want = (now.year, now.month, now.day, now.hour, now.minute, now.second)
        drift = (datetime(*panel) - datetime(*want)).total_seconds()
        if abs(drift) <= 60:
            _LOGGER.debug("Connect: panel clock within %.0f s", abs(drift))
            return False
        _LOGGER.info("Connect: panel clock is %.0f s %s; setting it", abs(drift), "ahead" if drift > 0 else "behind")
        if not await self.client.set_date_time(want):
            raise PanelError("the panel refused the new time")
        return True

    # ─── Arm / disarm ───────────────────────────────────────────────────────

    def _arm_type(self, mode: str) -> int:
        if mode == "away":
            return P.ARM_FULL
        part_arm = self.part_arms.get(mode)
        if not part_arm:
            raise PanelError(f"{mode} isn't set up: choose which part arm it uses in the integration's options")
        return part_arm

    async def arm(self, area: int, mode: str) -> None:
        """Disarms first when switching arm mode (as texecom2mqtt does)."""
        arm_type = self._arm_type(mode)
        client = self._ready_client()
        current = self.areas[area].state
        self._requested_mode[area] = mode
        self._last_user = ("Home Assistant", time.monotonic())
        try:
            if current not in (DISARMED, ARMING, PENDING, TRIGGERED):
                await self._ok(client.disarm(area, self.panel_zones), "disarm before re-arm")
            await self._ok(client.arm(area, arm_type, self.panel_zones), f"arm ({mode})")
        except PanelError:
            self._requested_mode.pop(area, None)
            raise

    async def disarm(self, area: int) -> None:
        """Resets first when in alarm (as texecom2mqtt does)."""
        client = self._ready_client()
        self._last_user = ("Home Assistant", time.monotonic())
        if self.areas[area].state == TRIGGERED:
            await self._ok(client.reset(area, self.panel_zones), "reset")
        await self._ok(client.disarm(area, self.panel_zones), "disarm")

    def _ready_client(self) -> ConnectClient:
        if not self.connected or not self.client:
            raise PanelError("not connected to the panel")
        return self.client

    @staticmethod
    async def _ok(request, what: str) -> None:
        try:
            ok = await request
        except ConnectError as err:
            raise PanelError(f"{what} failed: {err}") from err
        if not ok:
            raise PanelError(f"the panel refused {what}")

    def diagnostics(self) -> dict[str, Any]:
        return {
            "protocol": "connect",
            "single_flag_reads": bool(self.client and self.client.single_flag_reads),
            "last_error": self.last_error,
            "power": self.extra.get("power").__dict__ if self.extra.get("power") else None,
        }
