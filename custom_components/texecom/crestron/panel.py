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
from .connection import (
    LOGIN_TIMEOUT,
    POST_LOGOUT_BLACKOUT,
    RECONNECT_MIN,
    CommandFailed,
    ConnectionMixin,
    ConnectUnanswered,
)

_LOGGER = logging.getLogger(__name__)

# After a UDL session the panel releases held-back events in one burst (e.g.
# X, A, D for an arm followed by a quick disarm); X and A wait this long so a
# following event can replace them instead of flashing a stale state.
EVENT_COALESCE = 0.5
# ASTATUS doesn't report the exit delay; trust it over "arming" after this.
ARMING_GRACE = 120.0
# Power readings (through Texecom Connect, in a UDL session, so each costs
# ~30 s of held-back events): first a while after connecting, then this often.
POWER_POLL = 1800.0
POWER_FIRST_DELAY = 120.0
# When a read is skipped (an area is arming, in entry or in alarm), or fails,
# try again after this.
POWER_RETRY = 300.0
# After an arm from Home Assistant, the stored event log is read this long
# later (past a Part Arm's 10 s exit delay) to see whether the arm failed:
# Crestron's text feed says nothing when it does.
AFTER_ARM_CHECK = 20.0
# ...and again, this often, while the log shows the exit delay still running
# (Exit Started with nothing after it) or nothing yet: a full arm's exit delay
# can be longer. Each read is a session, so at most this many.
AFTER_ARM_TRIES = 6
LOG_EXIT_STARTED = 32
# Log event types: an arm that failed (one "Arm Failed" per zone in the way,
# after "Exit Error").
LOG_EXIT_ERROR = 33
LOG_ARM_FAILED = 85
# After the log shows a failed arm, the panel still releases the held-back
# "exit delay started" when its text feed resumes (seen on a real panel 30 s
# later); it's ignored until this long after the feed is due back.
HELD_EXIT_GRACE = 10.0


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
        login_timeout: float = LOGIN_TIMEOUT,
        power_poll: float = POWER_POLL,
        power_first_delay: float = POWER_FIRST_DELAY,
        power_retry: float = POWER_RETRY,
        after_arm_check: float = AFTER_ARM_CHECK,
        session_reads: bool = False,
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
        self.login_timeout = login_timeout
        self.power_poll = power_poll
        self.power_first_delay = power_first_delay
        self.power_retry = power_retry
        self._power_task: asyncio.Task | None = None
        self.after_arm_check = after_arm_check
        # Power and log readings every power_poll seconds: an option, off by
        # default, as each one holds back the panel's text feed for a while
        # (~30 s on an Elite 168, over 2 minutes on an Elite 24).
        self.session_reads = session_reads
        self._after_arm_task: asyncio.Task | None = None
        # The stored event log (read through Texecom Connect): its entry size
        # and ring size for this panel, and the next index to read.
        # The panel's size (from its identification, read over Connect the
        # first time it's needed) and, from that, the stored log's layout.
        self.panel_zones: int | None = None
        self.log_entry_size: int | None = None
        self.log_capacity: int | None = None
        self._log_pointer: int | None = None
        self._stale_exit_until: dict[int, float] = {}  # area -> ignore a held-back "X" until then
        self._writer: asyncio.StreamWriter | None = None
        self._task: asyncio.Task | None = None
        self._stopped = False
        self._lock = asyncio.Lock()
        self._splitter = P.LineSplitter(self._on_line, self._on_frame, self._on_connect_frame)
        self._connect_sequence = 0
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
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self._run(), name="texecom crestron")
        if self.udl and self.session_reads and self.power_poll > 0:
            self._power_task = loop.create_task(self._power_loop(), name="texecom crestron power")

    async def stop(self) -> None:
        self._stopped = True
        for handle in self._deferred.values():
            handle.cancel()
        for task in (self._after_arm_task, self._power_task, self._task):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self.set_connected(False)
        self.cancel_timers()

    # ─── Power ──────────────────────────────────────────────────────────────

    def _settled(self) -> bool:
        """No area arming, in entry or in alarm: nobody is likely to be at a
        keypad, so a short UDL session can't get in their way."""
        return self.connected and all(
            a.known and a.state not in (ARMING, PENDING, TRIGGERED) for a in self.areas.values()
        )

    async def _power_loop(self) -> None:
        await asyncio.sleep(self.power_first_delay)
        while True:
            if not self._settled() or self._busy:
                await asyncio.sleep(self.power_retry)
                continue
            # Every attempt holds back the panel's events for ~30 s, so a
            # failed one waits for the next regular reading, not the retry.
            try:
                power, entries = await self._session_reads(power=True, log=True)
            except ConnectUnanswered as err:
                _LOGGER.warning(
                    "Crestron: %s; power and log readings stop until Home Assistant restarts or reloads", err
                )
                return
            except (PanelError, CommandFailed) as err:
                _LOGGER.debug("Crestron: power and log reading failed: %s", err)
            else:
                if power:
                    self.extra["power"] = power
                    self.notify()
                self._on_log_entries(entries)
            await asyncio.sleep(self.power_poll)

    # ─── Event log ──────────────────────────────────────────────────────────

    async def _check_log_after_arm(self) -> None:
        last: int | None = None  # the newest log type read since the arm
        for _try in range(AFTER_ARM_TRIES):
            await asyncio.sleep(self.after_arm_check)
            try:
                _power, entries = await self._session_reads(power=False, log=True)
            except (PanelError, CommandFailed) as err:
                _LOGGER.debug("Crestron: reading the log after the arm failed: %s", err)
                return
            self._on_log_entries(entries)
            if entries:
                last = entries[-1][1]["type"]
            # Decided (armed, or failed and back to Off), or no longer arming.
            if (last is not None and last != LOG_EXIT_STARTED) or not any(
                a.state == ARMING for a in self.areas.values()
            ):
                return
        _LOGGER.debug("Crestron: the log showed no outcome for the arm after %d reads", AFTER_ARM_TRIES)

    def _on_log_entries(self, entries: list[tuple[int, dict[str, Any]]]) -> None:
        """Each stored log entry becomes a "log" event; a failed arm also an
        "arm_failed" event (as Texecom Connect gives), and the area no longer
        shows Arming."""
        for index, m in entries:
            _LOGGER.debug("Crestron: log entry %d: %s", index, P.C.describe_log(m))
            t = m["time"]
            self.on_event(
                "log",
                {
                    "log_type": m["type"],
                    "name": P.C.LOG_EVENT_NAMES.get(m["type"], f"Log event {m['type']}"),
                    "group": m["group"],
                    "parameter": m["parameter"],
                    "areas": m["areas"],
                    "communicated": m["communicated"],
                    "panel_time": "{year:04d}-{month:02d}-{day:02d}T{hours:02d}:{minutes:02d}:{seconds:02d}".format(
                        **t
                    ),
                    "index": index,
                },
            )
            if m["type"] == LOG_ARM_FAILED:
                zone = self.zones.get(m["parameter"])
                _LOGGER.warning("Crestron: arming failed: zone %s active", m["parameter"])
                self.on_event(
                    "arm_failed",
                    {"areas": m["areas"], "zone": m["parameter"], "zone_name": zone.name if zone else None},
                )
            if m["type"] in (LOG_EXIT_ERROR, LOG_ARM_FAILED):
                for number, area in self.areas.items():
                    if m["areas"] & (1 << (number - 1)) and area.state == ARMING:
                        self._requested_mode.pop(number, None)
                        self.set_area(number, DISARMED)
                        self._stale_exit_until[number] = max(self._blackout_until, time.monotonic()) + HELD_EXIT_GRACE

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
        if event == "X" and time.monotonic() < self._stale_exit_until.get(number, 0.0):
            # The exit delay of the arm the log already showed had failed.
            _LOGGER.debug("Crestron: ignoring the held-back exit delay of area %s's failed arm", number)
            return
        self._stale_exit_until.pop(number, None)
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
        # User 0 is nobody: e.g. the event held back after Home Assistant's
        # own UDL arm ("A0010"), which mustn't replace "Home Assistant".
        by = f"User {int(user)}" if user.isdigit() and int(user) else None
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
            arm_type = P.C.ARM_FULL
        else:
            part_arm = self.part_arms.get(mode)
            if not part_arm:
                raise PanelError(f"{mode} isn't set up: choose which part arm it uses in the integration's options")
            try:
                command, fallback = P.part_arm_frame(area, part_arm), None
            except ValueError as err:
                raise PanelError(str(err)) from err
            arm_type = part_arm  # Connect's part arm types are the part arm numbers (1-3)
        self._requested_mode[area] = mode
        self._arming_since[area] = time.monotonic()
        try:
            pointer = await self._transaction(
                command, fallback, lambda zones: (P.C.CMD_ARM_AREA, P.C.encode_arm(area, arm_type, zones))
            )
        except PanelError:
            self._requested_mode.pop(area, None)
            raise
        if pointer is not None:
            # Went through Texecom Connect: read the log once the exit delay
            # is over, from where it ended (if nothing was read before).
            if self._log_pointer is None:
                self._log_pointer = pointer
            if self._after_arm_task and not self._after_arm_task.done():
                self._after_arm_task.cancel()
            self._after_arm_task = asyncio.get_running_loop().create_task(
                self._check_log_after_arm(), name="texecom crestron after arm"
            )
        # The panel's own events are held back for ~30 s after the session.
        if self.areas[area].state in (DISARMED, TRIGGERED):
            self.set_area(area, ARMING, None, "Home Assistant")

    async def disarm(self, area: int) -> None:
        try:
            command = "D" + P.area_bitmask(area)
        except ValueError as err:
            raise PanelError(str(err)) from err
        await self._transaction(
            command,
            P.udl_frame_for("D", area),
            lambda zones: (P.C.CMD_DISARM_AREA, P.C.encode_disarm_or_reset(area, zones)),
        )
        self._requested_mode.pop(area, None)
        self.set_area(area, DISARMED, None, "Home Assistant")

    def diagnostics(self) -> dict[str, Any]:
        return {
            "protocol": "crestron",
            "connection": "serial" if self.serial_device else "network",
            "in_blackout": self.in_blackout,
            "last_error": self.last_error,
        }
