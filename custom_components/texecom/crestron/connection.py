"""The Crestron connection: opening the COM port (over the network or a
serial cable), reading from it and reconnecting with back-off, the status
poll that checks the panel is still answering, and sending commands,
including the UDL session arming needs (login, one command, logout)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from typing import Any

from ..panel import PanelError
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


class CommandFailed(PanelError):
    pass


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

    def _on_frame(self, frame: bytes) -> None:
        for waiter in list(self._waiters):
            waiter("frame", frame)

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
