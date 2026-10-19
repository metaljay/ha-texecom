"""The Crestron connection: opening the COM port (over the network or a
serial cable), reading from it and reconnecting with back-off, the status
poll that checks the panel is still answering, and sending commands,
including the UDL session arming needs (login, one command, logout)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from ..panel import PanelError, PanelNotConnected
from . import protocol as P

_LOGGER = logging.getLogger(__name__)

COMMAND_TIMEOUT = 5.0
# A real panel took 3-6 s to answer a UDL login with OK, and often never does
# although the session opens, so a missing OK isn't fatal.
LOGIN_TIMEOUT = 8.0
CONNECT_TIMEOUT = 10.0
RECONNECT_MIN = 5.0
RECONNECT_MAX = 30.0
# A bridge that's down fails every attempt: the first few failures in a row
# are logged at debug level, then one warning.
QUIET_FAILURES = 3
# Measured: the text feed resumes ~30 s after a Wintex logout.
POST_LOGOUT_BLACKOUT = 35.0
# At most this many stored log entries are read in one session (5 per read);
# after a longer gap the oldest of them are skipped.
MAX_LOG_CATCH_UP = 30
SILENT_POLLS_BEFORE_RECONNECT = 3


class CommandFailed(PanelError):
    pass


class CommandRefused(CommandFailed):
    """The panel answered and said no (ERROR or NAK), as opposed to not
    answering: only a refusal is safe to retry another way."""


class ConnectionMixin:
    """Part of CrestronPanel: the connection and sending commands."""

    # Attributes of the panel these methods use.
    udl: str | None
    host: str | None
    port: int | None
    serial_device: str | None
    baud_rate: int
    status_poll: float
    reconnect_min: float
    blackout: float
    last_error: str | None
    _writer: asyncio.StreamWriter | None
    _stopped: bool
    _lock: asyncio.Lock
    _splitter: P.LineSplitter
    _waiters: list[Callable[[str, Any], bool]]
    _last_data: float
    _blackout_until: float
    _busy: bool
    _first_status_pending: bool
    _connect_sequence: int
    login_timeout: float
    _log_pointer: int | None
    log_entry_size: int | None
    log_capacity: int | None

    @property
    def description(self) -> str:
        return f"serial {self.serial_device}" if self.serial_device else f"{self.host}:{self.port}"

    async def _open(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if self.serial_device:
            # Loaded lazily so network-only installs never need it.
            from serial_asyncio_fast import open_serial_connection

            return await open_serial_connection(url=self.serial_device, baudrate=self.baud_rate)
        return await asyncio.wait_for(asyncio.open_connection(self.host, self.port), CONNECT_TIMEOUT)

    async def _run(self) -> None:
        delay = self.reconnect_min
        failures = 0
        while not self._stopped:
            self._splitter.reset()
            try:
                reader, writer = await self._open()
            except (OSError, TimeoutError, ImportError) as err:
                self.last_error = f"cannot open {self.description}: {err}"
                failures += 1
                if failures == QUIET_FAILURES + 1:
                    _LOGGER.warning("Crestron: %s; still trying", self.last_error)
                else:
                    _LOGGER.debug("Crestron: %s; retrying", self.last_error)
            else:
                failures = 0
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
                    _LOGGER.info("Crestron: connection error: %s", err)
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
                    _LOGGER.info("Crestron: connection to %s closed; reconnecting", self.description)
            if self._stopped:
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, max(RECONNECT_MAX, self.reconnect_min))

    @property
    def in_blackout(self) -> bool:
        return time.monotonic() < self._blackout_until

    def _write(self, data: bytes) -> None:
        if not self._writer:
            raise PanelNotConnected("not connected to the panel")
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

    def _on_frame(self, frame: bytes) -> None:
        for waiter in list(self._waiters):
            waiter("frame", frame)

    # ─── Commands ───────────────────────────────────────────────────────────

    async def _expect(
        self,
        send: Callable[[], None],
        match: Callable[[str, Any], str | None],
        timeout: float,
        refused: type[CommandFailed] = CommandFailed,
    ) -> None:
        """Sends and waits until match() returns "ok" or an error message."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()

        def waiter(kind: str, value: Any) -> bool:
            if future.done():
                return True
            if kind == "closed":
                future.set_exception(PanelNotConnected("the connection to the panel closed"))
                return True
            result = match(kind, value)
            if result == "ok":
                future.set_result(None)
            elif result:
                future.set_exception(refused(result))
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

        await self._expect(lambda: self._write(P.encode_command(command)), match, timeout, CommandRefused)

    async def _send_frame(self, frame: bytes) -> None:
        def match(kind: str, reply: Any) -> str | None:
            if kind == "frame" and reply[1] == P.WINTEX_ACK:
                return "ok"
            if kind == "frame" and reply[1] == P.WINTEX_NAK:
                return "the panel refused the command"
            return None

        await self._expect(lambda: self._write(frame), match, COMMAND_TIMEOUT, CommandRefused)

    def _on_connect_frame(self, frame: Any) -> None:
        for waiter in list(self._waiters):
            waiter("connect", frame)

    async def _connect_request(self, command: int, body: bytes = b"") -> bytes:
        """One Texecom Connect command inside the UDL session. Returns its
        reply payload (after the echoed command byte)."""
        sequence = self._connect_sequence
        self._connect_sequence = (sequence + 1) & 0xFF
        payload: list[bytes] = []

        def match(kind: str, frame: Any) -> str | None:
            if kind != "connect" or frame.type != P.C.TYPE_RESPONSE or frame.body[:1] != bytes([command]):
                return None
            payload.append(frame.body[1:])
            return "ok"

        await self._expect(lambda: self._write(P.connect_command(sequence, command, body)), match, COMMAND_TIMEOUT)
        return payload[0]

    async def _send_connect(self, command: int, body: bytes, refused: str) -> None:
        """A Connect command whose reply is ACK or NAK."""
        reply = await self._connect_request(command, body)
        if reply[:1] == bytes([P.C.NAK]):
            raise CommandRefused(refused)
        if reply[:1] != bytes([P.C.ACK]):
            raise CommandFailed(f"unexpected reply {reply.hex()} from the panel")

    @contextlib.asynccontextmanager
    async def _udl_session(self) -> AsyncIterator[None]:
        """UDL login, the caller's commands, logout. A \\W<udl>/ login opens a
        Wintex session that silences the text feed; logging out shortens that
        to ~30 s. Sessions never overlap, and are never held open."""
        if not self.udl:
            raise PanelError("arming needs the UDL code: add it in the integration's options")
        async with self._lock:
            if not self._writer:
                raise PanelNotConnected("not connected to the panel")
            self._busy = True
            try:
                try:
                    # Sent once: a resend would open a second session.
                    await self._send_text(f"W{self.udl}", self.login_timeout)
                except CommandFailed as err:
                    if "timed out" not in str(err):
                        raise
                    _LOGGER.debug("Crestron: no OK to the UDL login; continuing (the session usually opens)")
                yield
            finally:
                if self._writer:
                    self._writer.write(P.WINTEX_LOGOUT)
                    self._blackout_until = time.monotonic() + self.blackout
                self._busy = False

    async def _transaction(
        self, command: str | bytes, fallback: bytes | None, connect: tuple[int, bytes] | None = None
    ) -> int | None:
        """One arm or disarm command in a UDL session. Returns the event log's
        pointer when the command went through Texecom Connect (read in the
        same session, so a later read can find what the command caused).

        connect is the same command as a Texecom Connect (command, body), tried
        last inside the same session: a Premier Elite 168 (V6.06.03) refuses
        both the text and the binary UDL arm/disarm commands but carries out
        Connect ones after a Connect login with the UDL code."""
        async with self._udl_session():
            try:
                if isinstance(command, bytes):
                    await self._send_frame(command)
                else:
                    try:
                        await self._send_text(command)
                    except CommandRefused:
                        # Seen when the login got no OK: text commands get
                        # ERROR while binary frames work.
                        if not fallback:
                            raise
                        _LOGGER.debug("Crestron: ERROR to the text command; sending the binary equivalent")
                        await self._send_frame(fallback)
            except CommandRefused as err:
                # Only a refusal: after no answer the command may still have
                # been carried out, and a second one could arm twice.
                if connect is None:
                    raise
                _LOGGER.debug("Crestron: %s; trying the Texecom Connect equivalent in the same session", err)
                await self._send_connect(P.C.CMD_LOGIN, self.udl.encode("latin1"), "the panel refused the UDL code")
                await self._send_connect(*connect, "the panel refused the command")
                if self.log_entry_size:
                    with contextlib.suppress(CommandFailed):
                        return await self._log_pointer_now()
        return None

    async def _log_pointer_now(self) -> int | None:
        reply = await self._connect_request(P.C.CMD_GET_LOG_POINTER)
        return int.from_bytes(reply, "little") if len(reply) == 2 else None

    async def _read_new_log_entries(self) -> list[tuple[int, dict[str, Any]]]:
        """The stored log entries written since the last read, oldest first, as
        (index, decoded entry). The first read only notes where the log ends.
        Inside a session, after the Connect login."""
        size, capacity = self.log_entry_size, self.log_capacity
        if not size or not capacity:
            return []
        pointer = await self._log_pointer_now()
        if pointer is None or not 0 <= pointer < capacity:
            return []
        if self._log_pointer is None:
            self._log_pointer = pointer
            return []
        count = (pointer - self._log_pointer) % capacity
        if count > MAX_LOG_CATCH_UP:
            _LOGGER.info("Crestron: %d new log entries; reading only the last %d", count, MAX_LOG_CATCH_UP)
            self._log_pointer = (pointer - MAX_LOG_CATCH_UP) % capacity
            count = MAX_LOG_CATCH_UP
        entries: list[tuple[int, dict[str, Any]]] = []
        index = self._log_pointer
        while count:
            # A read never runs past the end of the ring.
            chunk = min(count, P.C.MAX_LOG_EVENTS_PER_READ, capacity - index)
            reply = await self._connect_request(P.C.CMD_GET_LOG_EVENTS, P.C.encode_get_log_events(index, chunk))
            decoded = P.C.decode_log_entries(reply, size)
            if decoded is None or len(decoded) != chunk:
                _LOGGER.debug("Crestron: log read at %d refused (%s); trying again next time", index, reply.hex())
                break
            entries += [((index + n) % capacity, m) for n, m in enumerate(decoded)]
            index = (index + chunk) % capacity
            count -= chunk
        self._log_pointer = index
        return entries

    async def _session_reads(
        self, power: bool, log: bool
    ) -> tuple[P.C.SystemPower | None, list[tuple[int, dict[str, Any]]]]:
        """Power readings and new log entries, through Texecom Connect in one
        UDL session (Crestron itself has neither)."""
        reading, entries = None, []
        async with self._udl_session():
            await self._send_connect(P.C.CMD_LOGIN, self.udl.encode("latin1"), "the panel refused the UDL code")
            if power:
                reading = P.C.decode_system_power(await self._connect_request(P.C.CMD_GET_SYSTEM_POWER))
            if log:
                entries = await self._read_new_log_entries()
        return reading, entries
