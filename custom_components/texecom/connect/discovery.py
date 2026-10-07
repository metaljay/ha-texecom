"""Reading the panel's layout: its identity, the zones in use and the areas
that contain them. Done when setting up (probe: a one-off login), over the
open session when asked to in the options, and by itself when engineer
programming ends."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from ..panel import PanelArea, PanelError, PanelInfo, PanelZone
from .client import ConnectClient, ConnectError, HostLog, LoginRejected, Unreachable

_LOGGER = logging.getLogger(__name__)


def default_area_name(number: int) -> str:
    """Area A, B... as on the keypad; numbers beyond Z."""
    return f"Area {chr(64 + number)}" if number <= 26 else f"Area {number}"


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
        areas.append(PanelArea(number, (details and details.name) or default_area_name(number)))
    return info, zones, areas


PROBE_PATIENCE = 75.0  # seconds a SmartCom may take to free its session


async def probe(
    host: str, port: int, udl: str, patience: float | None = None
) -> tuple[PanelInfo, list[PanelZone], list[PanelArea]]:
    """One-off login and discovery (used when setting up).

    A SmartCom refuses a new session for about a minute after the last one
    closed (e.g. Homebridge just stopped), so keep trying for `patience`
    seconds unless nothing answers at all or the UDL code is wrong.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + (PROBE_PATIENCE if patience is None else patience)
    while True:
        client = ConnectClient(host, port, udl)
        try:
            await client.connect()
            try:
                return await discover(client)
            finally:
                await client.close()
        except (Unreachable, LoginRejected):
            raise
        except ConnectError as err:
            if loop.time() + 10 > deadline:
                raise
            _LOGGER.debug("Connect: panel busy while setting up (%s); trying again", err)
            await asyncio.sleep(10)


class RediscoveryMixin:
    """Part of ConnectPanel: reading zones and areas again over its session."""

    # Attributes of the panel these methods use.
    client: ConnectClient | None
    zones: dict[int, PanelZone]
    areas: dict[int, PanelArea]
    on_layout_changed: Callable[[PanelInfo, list[PanelZone], list[PanelArea]], None] | None
    _log: HostLog

    async def async_rediscover(self) -> tuple[PanelInfo, list[PanelZone], list[PanelArea]]:
        """Reads zones and areas again over the open session (a SmartCom
        allows only one, so a separate login would fail). Raises PanelError."""
        client = self._ready_client()
        try:
            return await discover(client)
        except ConnectError as err:
            raise PanelError(f"couldn't read the panel: {err}") from err

    async def _rediscover(self) -> None:
        if not self.client:
            return
        try:
            info, zones, areas = await discover(self.client)
        except ConnectError as err:
            self._log.warning("Connect: re-reading zones failed: %s", err)
            return

        def layout(zs, ars):
            return ([(z.number, z.name, z.panel_type, z.areas) for z in zs], [(a.number, a.name) for a in ars])

        if layout(zones, areas) != layout(self.zones.values(), self.areas.values()) and self.on_layout_changed:
            self.on_layout_changed(info, zones, areas)
