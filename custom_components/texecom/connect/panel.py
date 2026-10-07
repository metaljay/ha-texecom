"""Texecom Connect panel driver: the session (connecting, reconnecting and
keeping it alive), zone and area state, and arm/disarm.

ConnectPanel is put together from compartments, one module each:
discovery.py (reading zones and areas), events.py (what the panel's messages
mean), conditions.py (tamper, faults, mains) and clock.py (the panel clock).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from datetime import tzinfo
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
    PanelNotConnected,
    PanelRefused,
    PanelZone,
    TexecomPanel,
)
from . import protocol as P
from .client import ConnectClient, ConnectError, HostLog, LoginRejected, PanelBusyError, Timing
from .clock import ClockMixin
from .conditions import ConditionsMixin
from .discovery import RediscoveryMixin
from .events import USER_CHANGE_WINDOW, EventsMixin

_LOGGER = logging.getLogger(__name__)

RECONNECT_MIN = 5.0
RECONNECT_MAX = 30.0
READY_CHECK_DELAY = 1.5  # after zones settle, re-read whether an area is ready to arm
POWER_EVERY_N_IDLE = 1  # voltages and currents every keep-alive (~30 s)
SWITCH_GRACE = 10.0
# A SmartCom refuses a new session for about a minute after the last one
# closed (setup's check, a restart, an alarm report): the first few retries
# are expected, so only later ones are logged as warnings.
QUIET_FAILURES = 3  # longest a mode switch may sit between "disarmed" and the new exit delay


class ConnectPanel(RediscoveryMixin, EventsMixin, ConditionsMixin, ClockMixin, TexecomPanel):
    """Keeps one Connect session open, reconnecting with back-off."""

    reports_ready = True  # area flag 16, "Ready"

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
        on_clock_drift: Callable[[int | None], None] | None = None,
        timing: Timing | None = None,
        reconnect_min: float | None = None,
    ) -> None:
        super().__init__(part_arms)
        self.host, self.port, self.udl = host, port, str(udl)
        self._log = HostLog(_LOGGER, {"host": host})
        self.info = info
        self.zones = {z.number: z for z in zones}
        self.areas = {a.number: a for a in areas}
        self.time_zone = time_zone
        self.time_sync_seconds = max(0.0, min(float(time_sync_hours or 0), 744.0)) * 3600
        self.on_event = on_event or (lambda _t, _d: None)
        self.on_layout_changed = on_layout_changed
        self.on_auth_failed = on_auth_failed
        # Told the clock drift (seconds) once a session, when not syncing it.
        self.on_clock_drift = on_clock_drift
        self.timing = timing
        self.reconnect_min = RECONNECT_MIN if reconnect_min is None else reconnect_min
        self.client: ConnectClient | None = None
        self._task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self._stopped = False
        self._closed_event = asyncio.Event()
        self._last_part_arm: int | None = None
        self._last_user: tuple[str, float] | None = None
        self._recent_alarms: dict[tuple[int, bool], float] = {}
        self._alarm_zone: tuple[str, float] | None = None
        self._switching: dict[int, float] = {}
        self._seen_current = False
        self._idle_count = 0
        self._refresh_handle: asyncio.TimerHandle | None = None
        self._ready_handle: asyncio.TimerHandle | None = None
        self._logged_flags: dict[int, list[int]] = {}  # area -> flags last named in the debug log
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
        for handle in (self._refresh_handle, self._ready_handle):
            if handle:
                handle.cancel()
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
        self.cancel_timers()

    def _spawn(self, coro) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self) -> None:
        delay = self.reconnect_min
        failures = 0
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
                self._log.debug("Connect: connecting to %s:%s", self.host, self.port)
                await client.connect()
                await self._start_up()
                self._log.info("Connect: logged in to %s:%s", self.host, self.port)
                self.last_error = None
                delay = self.reconnect_min
                failures = 0
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
                self._log.error("Connect: %s", err)
                if self.on_auth_failed:
                    self.on_auth_failed()
                    self._stopped = True  # wait for a new code rather than retry
            except Exception as err:  # noqa: BLE001 - any failure means reconnect
                self.last_error = str(err)
                # A SmartCom is often busy for a minute (e.g. reporting an
                # alarm): one warning, then quieter until it's back.
                failures += 1
                if failures == QUIET_FAILURES + 1:
                    self._log.warning("Connect: %s; still trying", err)
                else:
                    self._log.debug("Connect: unavailable (%s); retrying", err)
            finally:
                await client.close()
                self.set_connected(False)
            if self._stopped:
                break
            self._log.debug("Connect: reconnecting in %.0f s", delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, max(RECONNECT_MAX, self.reconnect_min))

    async def _start_up(self) -> None:
        for attempt in range(3):
            try:
                await self.refresh()
                await self.read_power()
                await self.read_display()
                await self._check_clock()
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
        self._log_area_flags()
        self._update_ready()

    def _update_ready(self) -> None:
        flags = self.client.last_area_flags if self.client else None
        if flags:
            for number in self.areas:
                self.set_area_ready(number, P.FLAG_READY in P.area_flags_set(flags, number, self.panel_zones))

    def _check_ready_soon(self, zone_number: int) -> None:
        """A zone changed: once zones settle, re-read whether its areas are
        ready to arm (only while disarmed, when it matters; the panel doesn't
        announce it)."""
        zone = self.zones.get(zone_number)
        areas = (zone.areas if zone and zone.areas else None) or list(self.areas)
        if not any(self.areas[a].state == DISARMED for a in areas if a in self.areas):
            return
        if self._ready_handle:
            self._ready_handle.cancel()

        def run() -> None:
            self._ready_handle = None
            self._spawn(self._quiet(self.refresh_areas()))

        self._ready_handle = asyncio.get_running_loop().call_later(READY_CHECK_DELAY, run)

    def _log_area_flags(self) -> None:
        """Names the flags set for each area in the debug log when they change
        (most aren't used yet: this is how they get mapped on a real panel)."""
        flags = self.client.last_area_flags if self.client else None
        if not flags:
            return
        for number in self.areas:
            now_set = P.area_flags_set(flags, number, self.panel_zones)
            if self._logged_flags.get(number) != now_set:
                self._logged_flags[number] = now_set
                self._log.debug("Connect: area %s flags: %s", number, ", ".join(P.flag_names(now_set)) or "none")

    async def _on_idle(self) -> None:
        self._idle_count += 1
        await self.refresh()
        await self.read_display()
        if (self._idle_count - 1) % POWER_EVERY_N_IDLE == 0:
            await self.read_power()

    async def read_display(self) -> None:
        """What the keypads show, e.g. "System alerts" after an alarm."""
        assert self.client
        with contextlib.suppress(PanelBusyError):
            text = P.display_message(P.clean_text((await self.client.lcd_display()).encode("latin1")))
            if text and text != self.extra.get("display"):
                self.extra["display"] = text
                self.notify()

    async def read_power(self) -> None:
        assert self.client
        with contextlib.suppress(PanelBusyError):
            power = await self.client.system_power()
            if power:
                self.extra["power"] = power
                self._mains_from_power(power)
                self.notify()

    def _apply_area(self, number: int, state: str, part_arm: int | None, changed_by: str | None = None) -> None:
        area = self.areas.get(number)
        if area is None:
            return
        if state == "disarmed":
            if self._switching.get(number, 0) > time.monotonic():
                self._log.debug("Connect: area %s: disarmed while switching mode; still arming", number)
                return
            self._switching.pop(number, None)
            self._alarm_zone = None  # the next alarm names its own zone
            self.set_area(number, DISARMED, None, changed_by)
        elif state == "in exit":
            if self._switching.pop(number, None) is not None:
                self.set_area(number, ARMING, None, changed_by)
                return
            # An armed area can't start an exit delay without being disarmed
            # first; a flag re-read just after a remote arm can still show the
            # exit flag (seen on a real panel), so ignore it.
            if area.known and area.state not in (DISARMED, TRIGGERED, ARMING):
                self._log.debug("Connect: area %s: ignoring a stale exit flag while armed", number)
                return
            self.set_area(number, ARMING, None, changed_by)
        elif state == "in entry":
            self.set_area(number, PENDING, area.part_arm, changed_by)
        elif state == "armed":
            self.set_area(number, ARMED_AWAY, None, changed_by)
        elif state == "part armed":
            self.set_area(number, self.armed_state_for_part_arm(number, part_arm), part_arm, changed_by)
        elif state == "in alarm":
            if self._alarm_zone and time.monotonic() - self._alarm_zone[1] < USER_CHANGE_WINDOW:
                changed_by = self._alarm_zone[0]
            self.set_area(number, TRIGGERED, area.part_arm, changed_by)

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
            self._log.debug("Connect: area refresh skipped: %s", err)

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
                # Switching mode: the panel reports "disarmed" for a moment
                # before the new exit delay. Show "arming" throughout, so
                # automations on "disarmed" don't fire (seen on a real panel).
                self._switching[area] = time.monotonic() + SWITCH_GRACE
                self.set_area(area, ARMING, None, "Home Assistant")
                await self._ok(client.disarm(area, self.panel_zones), "disarm before re-arm")
            await self._ok(client.arm(area, arm_type, self.panel_zones), f"arm ({mode})")
        except PanelError:
            self._requested_mode.pop(area, None)
            if self._switching.pop(area, None) is not None:
                self._refresh_areas_soon()  # show whatever the panel is really in
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
            raise PanelNotConnected("not connected to the panel")
        return self.client

    @staticmethod
    async def _ok(request, what: str) -> None:
        try:
            ok = await request
        except ConnectError as err:
            raise PanelError(f"{what} failed: {err}") from err
        if not ok:
            raise PanelRefused(f"the panel refused {what}")

    def diagnostics(self) -> dict[str, Any]:
        return {
            "protocol": "connect",
            "single_flag_reads": bool(self.client and self.client.single_flag_reads),
            "last_error": self.last_error,
            "power": self.extra.get("power").__dict__ if self.extra.get("power") else None,
            "display": self.extra.get("display"),
            "faults": sorted(self.extra.get("faults", ())),
            "tampers": sorted(self.extra.get("tampers", ())),
            # Every flag the panel last reported for each area, by name.
            "area_flags": {
                number: P.flag_names(P.area_flags_set(self.client.last_area_flags, number, self.panel_zones))
                for number in self.areas
            }
            if self.client and self.client.last_area_flags
            else None,
        }
