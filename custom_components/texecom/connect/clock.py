"""The panel clock. A full power-down resets it, so it is checked on
connecting (reported through on_clock_drift when clock sync is off), set when
it drifts (clock sync, once a day), and shown in diagnostics. The panel keeps
local time, so Home Assistant's time zone is used."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, tzinfo
from typing import Any

from ..panel import PanelError
from . import protocol as P
from .client import ConnectClient, ConnectError, HostLog


class ClockMixin:
    """Part of ConnectPanel: the clock check, clock sync and the clock in
    diagnostics."""

    # Attributes of the panel these methods use.
    client: ConnectClient | None
    connected: bool
    time_zone: tzinfo | None
    time_sync_seconds: float
    on_clock_drift: Callable[[int | None], None] | None
    _log: HostLog

    async def _check_clock(self) -> None:
        """A full power-down resets the panel clock (seen on a real panel: to
        31 Oct 2023). With clock sync off, report how far out it is (None:
        the clock holds an impossible date)."""
        if self.time_sync_seconds > 0 or not self.on_clock_drift or not self.client:
            return
        try:
            clock = await self.client.date_time()
        except P.InvalidClock as err:
            self._log.warning("Connect: %s", err)
            self.on_clock_drift(None)
            return
        if clock:
            now = datetime.now(self.time_zone).replace(tzinfo=None, microsecond=0)
            self.on_clock_drift(int((datetime(*clock) - now).total_seconds()))

    async def _time_sync_loop(self) -> None:
        await asyncio.sleep(5)  # soon after connecting, e.g. after a power cut
        while True:
            try:
                await self.sync_clock()
            except (ConnectError, PanelError) as err:
                self._log.warning("Connect: clock check failed: %s", err)
            await asyncio.sleep(self.time_sync_seconds)

    async def sync_clock(self, now: datetime | None = None) -> bool:
        """Sets the panel clock if it is more than a minute out.

        The panel keeps local wall-clock time; Home Assistant's own time zone
        is used, so a Docker container running on UTC can't put the panel an
        hour out (a bug found in the Homebridge plugin).
        """
        assert self.client
        now = now or datetime.now(self.time_zone)
        want = (now.year, now.month, now.day, now.hour, now.minute, now.second)
        try:
            panel = await self.client.date_time()
        except P.InvalidClock as err:
            self._log.info("Connect: %s; setting it", err)
        else:
            if not panel:
                return False
            drift = (datetime(*panel) - datetime(*want)).total_seconds()
            if abs(drift) <= 60:
                self._log.debug("Connect: panel clock within %.0f s", abs(drift))
                return False
            self._log.info(
                "Connect: panel clock is %.0f s %s; setting it", abs(drift), "ahead" if drift > 0 else "behind"
            )
        if not await self.client.set_date_time(want):
            raise PanelError("the panel refused the new time")
        return True

    async def async_diagnostics(self) -> dict[str, Any]:
        """Like diagnostics(), plus what has to be asked for: the panel clock
        against local time, and the panel's system flags (raw: their meaning
        isn't mapped yet)."""
        result = self.diagnostics()
        if self.connected and self.client:
            try:
                flags = await self.client.system_flags()
            except ConnectError as err:
                result["system_flags"] = f"unreadable: {err}"
            else:
                result["system_flags"] = flags.hex(" ") if flags is not None else "refused"
            try:
                clock = await self.client.date_time()
            except (ConnectError, P.InvalidClock) as err:
                result["panel_clock"] = f"unreadable: {err}"
            else:
                if clock:
                    now = datetime.now(self.time_zone).replace(tzinfo=None, microsecond=0)
                    result["panel_clock"] = datetime(*clock).isoformat(sep=" ")
                    result["panel_clock_drift_s"] = int((datetime(*clock) - now).total_seconds())
        return result
