"""Texecom Connect client: one TCP session to a SmartCom/ComIP in normal mode."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from . import protocol as P

_LOGGER = logging.getLogger(__name__)

# Timings follow texecom2mqtt (3.5 s x 5, 2 s before login). A real panel
# took over 2.5 s to answer GET_AREA_FLAGS.
COMMAND_TIMEOUT = 3.5
COMMAND_ATTEMPTS = 5
KEEPALIVE = 30.0  # the panel drops a session after ~60 s without a command
LOGIN_DELAY = 2.0  # a login sent too soon after connecting is ignored
CONNECT_TIMEOUT = 10.0


class HostLog(logging.LoggerAdapter):
    """Puts the panel's address in every line, for homes (or test rigs) with
    more than one panel."""

    def process(self, msg, kwargs):
        return msg.replace("Connect: ", f"Connect {self.extra['host']}: ", 1), kwargs


class ConnectError(Exception):
    """The session failed (refused login, dropped connection, no answer)."""


class Unreachable(ConnectError):
    """Nothing answered at that address and port."""


class LoginRejected(ConnectError):
    """The panel refused the UDL code."""


class PanelBusyError(ConnectError):
    """The panel refused or garbled a read (e.g. a NAK just after a burst of
    events). Transient: skip this cycle, change nothing, don't reconnect."""


@dataclass
class Timing:
    # Defaults are read when a Timing is made, so tests can shorten them.
    command_timeout: float = field(default_factory=lambda: COMMAND_TIMEOUT)
    command_attempts: int = field(default_factory=lambda: COMMAND_ATTEMPTS)
    keepalive: float = field(default_factory=lambda: KEEPALIVE)
    login_delay: float = field(default_factory=lambda: LOGIN_DELAY)


class ConnectClient:
    """A single logged-in session. Reconnecting is the caller's job.

    on_message(dict) gets each decoded unsolicited message; on_close(reason)
    is called once when the session ends for any reason other than close().
    on_idle() runs every `keepalive` seconds (whether or not other commands
    were sent meanwhile) and must send at least one (the panel drops idle
    sessions).
    """

    def __init__(
        self,
        host: str,
        port: int,
        udl: str,
        on_message: Callable[[dict[str, Any]], None] = lambda _m: None,
        on_close: Callable[[str], None] = lambda _r: None,
        on_idle: Callable[[], Awaitable[Any]] | None = None,
        timing: Timing | None = None,
    ) -> None:
        self.host = host
        self._log = HostLog(_LOGGER, {"host": host})
        self.port = port
        self.udl = str(udl)
        self.on_message = on_message
        self.on_close = on_close
        self.on_idle = on_idle
        self.timing = timing or Timing()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task | None = None
        self._keepalive_task: asyncio.Task | None = None
        self._teardown_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._sequence = 0
        self._pending: tuple[int, int, asyncio.Future[bytes]] | None = None
        self._last_message_seq = -1
        self._closed = False
        self._last_command = 0.0
        self._parser = P.FrameParser(self._on_frame, self._on_drop, lambda m: self._log.debug("Connect: %s", m))
        self.single_flag_reads = False
        self._bulk_flag_failures = 0
        self.last_area_flags: bytes | None = None  # the last GET_AREA_FLAGS read, from flag 0

    @property
    def connected(self) -> bool:
        return self._writer is not None and not self._closed

    async def connect(self) -> None:
        """Open the socket, log in and subscribe to events."""
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), CONNECT_TIMEOUT
            )
        except (OSError, TimeoutError) as err:
            raise Unreachable(f"cannot reach {self.host}:{self.port} ({err})") from err
        self._read_task = asyncio.get_running_loop().create_task(self._read_loop())
        try:
            await asyncio.sleep(self.timing.login_delay)
            reply = await self.command(P.CMD_LOGIN, self.udl.encode("latin1"))
            if reply[:1] != bytes([P.ACK]):
                if reply[:1] == bytes([P.NAK]):
                    raise LoginRejected("the panel refused the UDL code")
                raise ConnectError(f"unexpected login reply {reply.hex()}")
            flags = P.EVENT_ZONE | P.EVENT_AREA | P.EVENT_OUTPUT | P.EVENT_USER | P.EVENT_LOG
            reply = await self.command(P.CMD_SET_EVENT_MESSAGES, bytes([flags & 0xFF, flags >> 8]))
            if reply[:1] != bytes([P.ACK]):
                raise ConnectError("the panel refused the event subscription")
        except LoginRejected:
            await self.close()
            raise
        except ConnectError as err:
            ended = self._closed
            await self.close()
            if ended:  # the panel closed the connection instead of answering
                raise ConnectError("the panel closed the connection (busy with another session?)") from err
            raise
        except BaseException:
            await self.close()
            raise
        self._keepalive_task = asyncio.get_running_loop().create_task(self._keepalive_loop())

    async def close(self) -> None:
        """Close without calling on_close."""
        self._closed = True
        await self._teardown("closed")

    async def _teardown(self, reason: str) -> None:
        current = asyncio.current_task()
        for task in (self._keepalive_task, self._read_task):
            if task and task is not current and not task.done():
                task.cancel()
        if self._pending and not self._pending[2].done():
            self._pending[2].set_exception(ConnectError(reason))
        if self._writer:
            writer, self._writer = self._writer, None
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    def _end(self, reason: str) -> None:
        """The session died: tear down and tell the owner once."""
        if self._closed:
            return
        self._closed = True
        self._log.info("Connect: session ended: %s", reason)
        # Kept: asyncio holds tasks weakly, and a socket left open stops the
        # panel (one session at a time) from accepting the next login.
        self._teardown_task = asyncio.get_running_loop().create_task(self._teardown(reason))
        self.on_close(reason)

    async def _read_loop(self) -> None:
        assert self._reader
        try:
            while True:
                data = await self._reader.read(1024)
                if not data:
                    break
                self._parser.push(data)
        except asyncio.CancelledError:
            raise
        except OSError as err:
            self._end(f"connection error: {err}")
            return
        self._end("connection closed by the panel")

    def _on_drop(self, reason: str) -> None:
        self._end(reason)

    def _on_frame(self, frame: P.Frame) -> None:
        if frame.type == P.TYPE_RESPONSE:
            pending = self._pending
            if not pending or frame.sequence != pending[0] or pending[2].done():
                self._log.debug("Connect: ignoring response with unexpected sequence %s", frame.sequence)
                return
            if frame.body[:1] != bytes([pending[1]]):
                pending[2].set_exception(
                    ConnectError(f"response for command {frame.body[:1].hex()}, expected {pending[1]}")
                )
                return
            pending[2].set_result(frame.body[1:])
        elif frame.type == P.TYPE_MESSAGE:
            if frame.sequence == self._last_message_seq:
                return  # duplicate
            self._last_message_seq = frame.sequence
            try:
                self.on_message(P.decode_message(frame.body))
            except Exception:  # never let a handler kill the session
                self._log.exception("Connect: error handling message %s", frame.body.hex())

    async def command(self, cmd: int, body: bytes = b"", *, optional: bool = False) -> bytes:
        """Send a command and return its reply payload (after the echoed
        command byte). Commands never overlap; each is resent on timeout with
        the same sequence number. An optional command (one the panel might
        not support) is sent once, and no answer doesn't end the session."""
        async with self._lock:
            if not self._writer or self._closed:
                raise ConnectError("not connected")
            sequence = self._sequence
            self._sequence = (self._sequence + 1) & 0xFF
            frame = P.encode_command(sequence, cmd, body)
            loop = asyncio.get_running_loop()
            self._last_command = loop.time()
            for _attempt in range(1 if optional else self.timing.command_attempts):
                future: asyncio.Future[bytes] = loop.create_future()
                self._pending = (sequence, cmd, future)
                try:
                    self._writer.write(frame)
                    return await asyncio.wait_for(future, self.timing.command_timeout)
                except TimeoutError:
                    if self._closed:
                        break
                    continue
                finally:
                    self._pending = None
            if optional:
                raise ConnectError(f"command {cmd} was not answered")
            reason = f"command {cmd} was not answered after {self.timing.command_attempts} attempts"
            # An unanswered command means the session is dead even if TCP isn't.
            self._end(reason)
            raise ConnectError(reason)

    async def _keepalive_loop(self) -> None:
        loop = asyncio.get_running_loop()
        last_idle = loop.time()
        while not self._closed:
            # Every interval, even when other commands were sent meanwhile:
            # re-reading "ready" as zones change (people moving about) would
            # otherwise put off the state, power and keypad reads for as long
            # as it lasts, and the mains coming back is only seen in those.
            wait = min(self._last_command, last_idle) + self.timing.keepalive - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
                continue
            try:
                if self.on_idle:
                    await self.on_idle()
                else:
                    await self.command(P.CMD_GET_DATE_TIME)
            except PanelBusyError as err:
                self._log.debug("Connect: state re-read skipped: %s", err)
            except ConnectError as err:
                self._log.debug("Connect: keep-alive failed: %s", err)
            except Exception:
                self._log.exception("Connect: keep-alive error")
            # Whatever happened, don't spin: wait at least a full interval.
            self._last_command = last_idle = max(self._last_command, loop.time())

    # ─── Reads ──────────────────────────────────────────────────────────────

    async def panel_identification(self) -> P.PanelIdentification:
        return P.decode_panel_identification(await self.command(P.CMD_GET_PANEL_IDENTIFICATION))

    async def zone_details(self, zone: int) -> P.ZoneDetails | None:
        body = zone.to_bytes(2, "little") if zone > 255 else bytes([zone])
        return P.decode_zone_details(await self.command(P.CMD_GET_ZONE_DETAILS, body))

    async def user_name(self, number: int) -> str | None:
        """User N's name ("" if it has none); None if the panel has no such
        user, or refuses. The reply also holds the user's code: it's dropped
        here, unread."""
        return P.decode_user_name(await self.command(P.CMD_GET_USER, bytes([number]), optional=True))

    async def area_details(self, area: int) -> P.AreaDetails | None:
        return P.decode_area_details(await self.command(P.CMD_GET_AREA_DETAILS, bytes([area])))

    async def lcd_display(self) -> str:
        """The keypad's two 16-character lines, joined with a space."""
        text = (await self.command(P.CMD_GET_LCD_DISPLAY)).decode("latin1")
        return " ".join(part.strip() for part in (text[:16], text[16:32]) if part.strip())

    async def system_power(self) -> P.SystemPower | None:
        return P.decode_system_power(await self.command(P.CMD_GET_SYSTEM_POWER))

    async def system_flags(self) -> bytes | None:
        """The panel's system flags (meaning not mapped yet; for diagnostics).
        None if the panel refuses."""
        reply = await self.command(P.CMD_GET_SYSTEM_FLAGS, optional=True)
        return None if P.is_nak(reply) else reply

    async def date_time(self) -> tuple[int, int, int, int, int, int] | None:
        return P.decode_date_time(await self.command(P.CMD_GET_DATE_TIME))

    async def set_date_time(self, parts: tuple[int, int, int, int, int, int]) -> bool:
        reply = await self.command(P.CMD_SET_DATE_TIME, P.encode_date_time(*parts))
        return reply[:1] == bytes([P.ACK])

    async def zone_states(self, panel_zones: int) -> dict[int, P.ZoneState]:
        """Current state of zones 1..panel_zones (one byte each)."""
        states: dict[int, P.ZoneState] = {}
        per_request = 168
        for start in range(1, panel_zones + 1, per_request):
            count = min(per_request, panel_zones - start + 1)
            data = await self.command(P.CMD_GET_ZONE_STATE, P.encode_get_zone_state(start, count, panel_zones))
            if len(data) != count:
                # A 1-byte NAK (0x15) would otherwise read as "zone active, alarmed".
                what = "panel replied NAK" if P.is_nak(data) else f"got {len(data)} of {count} bytes"
                raise PanelBusyError(f"zone states: {what}")
            for i in range(count):
                states[start + i] = P.decode_zone_state(data[i])
        return states

    async def area_states(self, areas: list[int], panel_zones: int) -> dict[int, tuple[str, int | None]]:
        """Current (state, part_arm) of the given areas.

        One bulk read where the panel supports it (Elite 24, V6.05.03: 72
        flags in 0.4 s). Some firmware (Elite 48, V4.02.01) answers a bulk
        read with a single byte; then the flags that matter are read one at
        a time. A short reply is never read as "disarmed".
        """
        size = P.area_bytes(panel_zones)
        count = 30 if size == 8 else 72
        if not self.single_flag_reads:
            flags = await self.command(P.CMD_GET_AREA_FLAGS, bytes([0, count]))
            if len(flags) == count * size:
                self._bulk_flag_failures = 0
                self.last_area_flags = flags
                return P.decode_area_flags(flags, areas, panel_zones)
            # A NAK can be transient, so only give up after repeated failures.
            self._bulk_flag_failures += 1
            what = "panel replied NAK" if P.is_nak(flags) else f"got {len(flags)} of {count * size} bytes"
            if self._bulk_flag_failures < 3:
                raise PanelBusyError(f"area flags: {what}")
            self._log.info("Connect: bulk area-flag reads keep failing (%s); switching to single-flag reads", what)
            self.single_flag_reads = True
        valid_bits = (1 << P.area_count(panel_zones)) - 1
        buf = bytearray(count * size)
        for flag in P.AREA_FLAGS:
            if size == 8 and flag >= 30:
                continue
            bitmap = await self.command(P.CMD_GET_AREA_FLAGS, bytes([flag, 1]))
            if len(bitmap) != size:
                raise PanelBusyError(f"area flag {flag}: expected {size} byte(s), got {len(bitmap)}")
            # With 1-byte bitmaps a NAK (0x15) only shows as bits for areas
            # the panel doesn't have.
            if int.from_bytes(bitmap, "little") & ~valid_bits:
                raise PanelBusyError(f"area flag {flag}: reply {bitmap.hex()} looks like a NAK")
            buf[flag * size : flag * size + size] = bitmap
        self.last_area_flags = bytes(buf)
        return P.decode_area_flags(bytes(buf), areas, panel_zones)

    # ─── Arm / disarm (adapted from texecom2mqtt, MIT) ──────────────────────

    async def arm(self, area: int, arm_type: int, panel_zones: int) -> bool:
        reply = await self.command(P.CMD_ARM_AREA, P.encode_arm(area, arm_type, panel_zones))
        return reply[:1] == bytes([P.ACK])

    async def disarm(self, area: int, panel_zones: int) -> bool:
        reply = await self.command(P.CMD_DISARM_AREA, P.encode_disarm_or_reset(area, panel_zones))
        return reply[:1] == bytes([P.ACK])

    async def reset(self, area: int, panel_zones: int) -> bool:
        reply = await self.command(P.CMD_RESET_AREA, P.encode_disarm_or_reset(area, panel_zones))
        return reply[:1] == bytes([P.ACK])
