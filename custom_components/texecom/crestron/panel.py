"""Crestron panel driver: a COM port set to "Crestron System", reached over a
serial-to-IP bridge (or a SmartCom/ComIP switched to Crestron) or a serial
port."""

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

_LOGGER = logging.getLogger(__name__)

COMMAND_TIMEOUT = 5.0
# A real panel took 3-6 s to answer a UDL login with OK, and often never does
# although the session opens, so a missing OK isn't fatal.
LOGIN_TIMEOUT = 8.0
CONNECT_TIMEOUT = 10.0
RECONNECT_MIN = 5.0
RECONNECT_MAX = 30.0
# Measured: the text feed resumes ~30 s after a Wintex logout.
POST_LOGOUT_BLACKOUT = 35.0
SILENT_POLLS_BEFORE_RECONNECT = 3
# After a UDL session the panel releases held-back events in one burst (e.g.
# X, A, D for an arm followed by a quick disarm); X and A wait this long so a
# following event can replace them instead of flashing a stale state.
EVENT_COALESCE = 0.5
# ASTATUS doesn't report the exit delay; trust it over "arming" after this.
ARMING_GRACE = 120.0


class CommandFailed(PanelError):
    pass


class CrestronPanel(TexecomPanel):
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

    @property
    def description(self) -> str:
        return f"serial {self.serial_device}" if self.serial_device else f"{self.host}:{self.port}"

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

    async def _open(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if self.serial_device:
            # Loaded lazily so network-only installs never need it.
            from serial_asyncio_fast import open_serial_connection

            return await open_serial_connection(url=self.serial_device, baudrate=self.baud_rate)
        return await asyncio.wait_for(asyncio.open_connection(self.host, self.port), CONNECT_TIMEOUT)

    async def _run(self) -> None:
        delay = self.reconnect_min
        while not self._stopped:
            self._splitter.reset()
            try:
                reader, writer = await self._open()
            except (OSError, TimeoutError, ImportError) as err:
                self.last_error = f"cannot open {self.description}: {err}"
                _LOGGER.warning("Crestron: %s", self.last_error)
            else:
                self._writer = writer
                self._last_data = time.monotonic()
                self.last_error = None
                delay = self.reconnect_min
                _LOGGER.info("Crestron: connected via %s", self.description)
                self.set_connected(True)
                poller = asyncio.get_running_loop().create_task(self._poll_loop())
                try:
                    while True:
                        data = await reader.read(1024)
                        if not data:
                            break
                        self._last_data = time.monotonic()
                        self._splitter.push(data)
                except OSError as err:
                    _LOGGER.warning("Crestron: connection error: %s", err)
                finally:
                    poller.cancel()
                    self._writer = None
                    writer.close()
                    with contextlib.suppress(Exception):
                        await writer.wait_closed()
                    self.set_connected(False)
                    for waiter in list(self._waiters):
                        waiter("closed", None)
                if not self._stopped:
                    _LOGGER.warning("Crestron: connection to %s closed", self.description)
            if self._stopped:
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, max(RECONNECT_MAX, self.reconnect_min))

    @property
    def in_blackout(self) -> bool:
        return time.monotonic() < self._blackout_until

    def _write(self, data: bytes) -> None:
        if not self._writer:
            raise CommandFailed("not connected to the panel")
        self._writer.write(data)

    def send_query(self, text: str) -> bool:
        if not self._writer or self.in_blackout:
            return False
        self._writer.write(f"{text}\r\n".encode("latin1"))
        return True

    async def _poll_loop(self) -> None:
        """ASTATUS on connect and then every status_poll seconds: a liveness
        check (a TCP connection only proves the bridge is up) and a correction
        for missed arm/disarm events."""
        self._first_status_pending = True
        self.send_query("ASTATUS")
        if self.status_poll <= 0:
            return
        while True:
            await asyncio.sleep(self.status_poll)
            if self._busy:
                continue
            quiet = time.monotonic() - max(self._last_data, self._blackout_until)
            if quiet > SILENT_POLLS_BEFORE_RECONNECT * self.status_poll:
                _LOGGER.warning("Crestron: no reply from the panel for %.0f s; reconnecting", quiet)
                if self._writer:
                    self._writer.close()
                return
            self.send_query("ASTATUS")

    # ─── Incoming ───────────────────────────────────────────────────────────

    def _on_frame(self, frame: bytes) -> None:
        for waiter in list(self._waiters):
            waiter("frame", frame)

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

    # ─── Commands ───────────────────────────────────────────────────────────

    async def _expect(self, send: Callable[[], None], match: Callable[[str, Any], str | None], timeout: float) -> None:
        """Sends and waits until match() returns "ok" or an error message."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()

        def waiter(kind: str, value: Any) -> bool:
            if future.done():
                return True
            if kind == "closed":
                future.set_exception(CommandFailed("connection closed"))
                return True
            result = match(kind, value)
            if result == "ok":
                future.set_result(None)
            elif result:
                future.set_exception(CommandFailed(result))
            return result is not None

        self._waiters.append(waiter)
        try:
            send()
            await asyncio.wait_for(future, timeout)
        except TimeoutError as err:
            raise CommandFailed("timed out waiting for the panel") from err
        finally:
            self._waiters.remove(waiter)

    async def _send_text(self, command: str, timeout: float = COMMAND_TIMEOUT) -> None:
        def match(kind: str, msg: Any) -> str | None:
            if kind == "line" and msg["type"] == "ok":
                return "ok"
            if kind == "line" and msg["type"] == "error":
                return "the panel rejected the command"
            return None

        await self._expect(lambda: self._write(P.encode_command(command)), match, timeout)

    async def _send_frame(self, frame: bytes) -> None:
        def match(kind: str, reply: Any) -> str | None:
            if kind == "frame" and reply[1] == P.WINTEX_ACK:
                return "ok"
            if kind == "frame" and reply[1] == P.WINTEX_NAK:
                return "the panel refused the command"
            return None

        await self._expect(lambda: self._write(frame), match, COMMAND_TIMEOUT)

    async def _transaction(self, command: str | bytes, fallback: bytes | None) -> None:
        """UDL login, one command, logout. A \\W<udl>/ login opens a Wintex
        session that silences the text feed; logging out shortens that to
        ~30 s. Transactions never overlap."""
        if not self.udl:
            raise PanelError("arming needs the UDL code: add it in the integration's options")
        async with self._lock:
            if not self._writer:
                raise PanelError("not connected to the panel")
            self._busy = True
            try:
                try:
                    # Sent once: a resend would open a second session.
                    await self._send_text(f"W{self.udl}", LOGIN_TIMEOUT)
                except CommandFailed as err:
                    if "timed out" not in str(err):
                        raise
                    _LOGGER.debug("Crestron: no OK to the UDL login; continuing (the session usually opens)")
                if isinstance(command, bytes):
                    await self._send_frame(command)
                else:
                    try:
                        await self._send_text(command)
                    except CommandFailed as err:
                        # Seen when the login got no OK: text commands get
                        # ERROR while binary frames work.
                        if not fallback or "rejected" not in str(err):
                            raise
                        _LOGGER.debug("Crestron: ERROR to the text command; sending the binary equivalent")
                        await self._send_frame(fallback)
            finally:
                if self._writer:
                    self._writer.write(P.WINTEX_LOGOUT)
                    self._blackout_until = time.monotonic() + self.blackout
                self._busy = False

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
