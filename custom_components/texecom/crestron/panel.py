"""Crestron panel driver: a COM port set to "Crestron System", reached over a
serial-to-IP bridge (or a SmartCom/ComIP switched to Crestron) or a serial
port.

This module turns what the panel sends into zone and area states, and arms
and disarms; connection.py has the connection itself and sends commands.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from typing import Any

from ..panel import (
    ARMED_STATE_FOR_MODE,
    ARMING,
    DISARMED,
    PENDING,
    TRIGGERED,
    PanelArea,
    PanelError,
    PanelZone,
    TexecomPanel,
)
from . import protocol as P
from .connection import POST_LOGOUT_BLACKOUT, RECONNECT_MIN, ConnectionMixin

_LOGGER = logging.getLogger(__name__)

# After a UDL session the panel releases held-back events in one burst (e.g.
# X, A, D for an arm followed by a quick disarm); X and A wait this long so a
# following event can replace them instead of flashing a stale state.
EVENT_COALESCE = 0.5
# ASTATUS doesn't report the exit delay; trust it over "arming" after this.
ARMING_GRACE = 120.0


class CrestronPanel(ConnectionMixin, TexecomPanel):
    def __init__(
        self,
        part_arms: dict[str, int],
        zones: list[PanelZone],
        areas: list[PanelArea],
        udl: str | None,
        host: str | None = None,
        port: int | None = None,
        serial_device: str | None = None,
        baud_rate: int = 19200,
        keypad_arm_mode: str = "away",
        status_poll: float = 60.0,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
        reconnect_min: float | None = None,
        event_coalesce: float | None = None,
        blackout: float | None = None,
    ) -> None:
        super().__init__(part_arms)
        self.zones = {z.number: z for z in zones}
        self.areas = {a.number: a for a in areas}
        self.udl = str(udl) if udl else None
        self.host, self.port = host, port
        self.serial_device, self.baud_rate = serial_device, baud_rate
        self.keypad_arm_mode = keypad_arm_mode if keypad_arm_mode in ARMED_STATE_FOR_MODE else "away"
        self.status_poll = status_poll
        self.on_event = on_event or (lambda _t, _d: None)
        self.reconnect_min = RECONNECT_MIN if reconnect_min is None else reconnect_min
        self.event_coalesce = EVENT_COALESCE if event_coalesce is None else event_coalesce
        self.blackout = POST_LOGOUT_BLACKOUT if blackout is None else blackout
        self._writer: asyncio.StreamWriter | None = None
        self._task: asyncio.Task | None = None
        self._stopped = False
        self._lock = asyncio.Lock()
        self._splitter = P.LineSplitter(self._on_line, self._on_frame)
        self._waiters: list[Callable[[str, Any], bool]] = []
        self._last_data = 0.0
        self._blackout_until = 0.0
        self._busy = False
        self._first_status_pending = False
        self._deferred: dict[int, asyncio.TimerHandle] = {}
        self._arming_since: dict[int, float] = {}
        self.last_error: str | None = None

    @property
    def can_control(self) -> bool:
        return bool(self.udl)

    # ─── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._stopped = False
        self._task = asyncio.get_running_loop().create_task(self._run(), name="texecom crestron")

    async def stop(self) -> None:
        self._stopped = True
        for handle in self._deferred.values():
            handle.cancel()
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        self.set_connected(False)
        self.cancel_timers()

    # ─── Incoming ───────────────────────────────────────────────────────────

    def _on_line(self, line: str) -> None:
        _LOGGER.debug("Crestron: received %r", line)
        try:
            msg = P.parse_line(line)
            for waiter in list(self._waiters):
                waiter("line", msg)
            kind = msg["type"]
            if kind == "zone":
                self.set_zone(msg["zone"], "active" if msg["status"] == "1" else "secure")
            elif kind == "area":
                self._on_area_event(msg["area"], msg["event"], msg["user"])
            elif kind == "user":
                self.on_event("user", {"user": msg["user"], "method": "code"})
            elif kind == "astatus":
                after_connect, self._first_status_pending = self._first_status_pending, False
                for index, armed in enumerate(msg["armed"]):
                    self._apply_armed_status(index + 1, armed, after_connect)
        except Exception:  # never let a malformed line take down the link
            _LOGGER.exception("Crestron: error handling %r", line)

    def _on_area_event(self, number: int, event: str, user: str) -> None:
        if (handle := self._deferred.pop(number, None)) is not None:
            handle.cancel()
        if event in ("X", "A") and self.event_coalesce > 0:
            self._deferred[number] = asyncio.get_running_loop().call_later(
                self.event_coalesce, self._apply_area_event, number, event, user
            )
            return
        self._apply_area_event(number, event, user)

    def _apply_area_event(self, number: int, event: str, user: str) -> None:
        self._deferred.pop(number, None)
        by = f"User {int(user)}" if user.isdigit() else None
        area = self.areas.get(number)
        if area is None:
            return
        if event == "X":
            self._arming_since[number] = time.monotonic()
            self.set_area(number, ARMING)
        elif event == "E":
            if area.state == TRIGGERED:
                # The entry zone seen again during an alarm (as Connect
                # reports it on a real panel): still an alarm until disarmed.
                _LOGGER.debug("Crestron: area %s in entry again during an alarm; still in alarm", number)
                return
            self.set_area(number, PENDING, area.part_arm)
        elif event == "L":
            self.set_area(number, TRIGGERED, area.part_arm)
        elif event == "D":
            self.set_area(number, DISARMED, None, by)
        elif event == "A":
            self.set_area(number, self._armed_state(number), None, by)

    def _armed_state(self, number: int) -> str:
        """Crestron doesn't say full or part: use the mode we asked for, else
        the keypad arm mode setting."""
        mode = self._requested_mode.get(number) or self.keypad_arm_mode
        return ARMED_STATE_FOR_MODE[mode]

    def _apply_armed_status(self, number: int, armed: bool, after_connect: bool) -> None:
        """Only corrects HA when it disagrees with the panel. The panel never
        reports an alarm ending, and an alarm can be raised in a disarmed area
        (24-hour zones, tamper), so a periodic "not armed" leaves Triggered
        alone; only the first reply after connecting clears it."""
        area = self.areas.get(number)
        if area is None:
            return
        arming_for = time.monotonic() - self._arming_since.get(number, 0.0)
        if not armed:
            if area.state == TRIGGERED and not after_connect:
                return
            if area.state in (ARMING, PENDING) and arming_for < ARMING_GRACE:
                return
            if area.state != DISARMED or not area.known:
                self.set_area(number, DISARMED)
        elif area.state == DISARMED or not area.known or (area.state == ARMING and arming_for >= ARMING_GRACE):
            self.set_area(number, self._armed_state(number))

    # ─── Arm / disarm ───────────────────────────────────────────────────────

    async def arm(self, area: int, mode: str) -> None:
        if mode == "away":
            command: str | bytes = "A" + P.area_bitmask(area)
            fallback = P.udl_frame_for("A", area)
        else:
            part_arm = self.part_arms.get(mode)
            if not part_arm:
                raise PanelError(f"{mode} isn't set up: choose which part arm it uses in the integration's options")
            try:
                command, fallback = P.part_arm_frame(area, part_arm), None
            except ValueError as err:
                raise PanelError(str(err)) from err
        self._requested_mode[area] = mode
        self._arming_since[area] = time.monotonic()
        try:
            await self._transaction(command, fallback)
        except PanelError:
            self._requested_mode.pop(area, None)
            raise
        # The panel's own events are held back for ~30 s after the session.
        if self.areas[area].state in (DISARMED, TRIGGERED):
            self.set_area(area, ARMING, None, "Home Assistant")

    async def disarm(self, area: int) -> None:
        try:
            command = "D" + P.area_bitmask(area)
        except ValueError as err:
            raise PanelError(str(err)) from err
        await self._transaction(command, P.udl_frame_for("D", area))
        self._requested_mode.pop(area, None)
        self.set_area(area, DISARMED, None, "Home Assistant")

    def diagnostics(self) -> dict[str, Any]:
        return {
            "protocol": "crestron",
            "connection": "serial" if self.serial_device else "network",
            "in_blackout": self.in_blackout,
            "last_error": self.last_error,
        }
