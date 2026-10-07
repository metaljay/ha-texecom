"""The Texecom integration: Premier Elite panels over Texecom Connect
(SmartCom/ComIP) or a COM port set to Crestron."""

from __future__ import annotations

import contextlib
import logging
from dataclasses import asdict
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from .connect.panel import ConnectPanel
from .const import (
    CONF_AREA_COUNT,
    CONF_AREAS,
    CONF_BAUD_RATE,
    CONF_CONNECTION,
    CONF_CREATE_DASHBOARD,
    CONF_HOME_PART_ARM,
    CONF_INFO,
    CONF_KEYPAD_ARM_MODE,
    CONF_NIGHT_PART_ARM,
    CONF_PROTOCOL,
    CONF_SERIAL_DEVICE,
    CONF_STATUS_POLL,
    CONF_TIME_SYNC,
    CONF_UDL,
    CONF_ZONE_COUNT,
    CONF_ZONES,
    CONNECTION_SERIAL,
    DEFAULT_BAUD_RATE,
    DEFAULT_STATUS_POLL,
    EVENT,
    PROTOCOL_CONNECT,
    TIME_SYNC_HOURS,
)
from .crestron.panel import CrestronPanel
from .dashboard import async_create_dashboard
from .entity import panel_device_info
from .panel import PanelArea, PanelInfo, PanelZone, TexecomPanel

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.ALARM_CONTROL_PANEL, Platform.BINARY_SENSOR, Platform.SENSOR]

type TexecomConfigEntry = ConfigEntry[TexecomPanel]


def zones_from_data(data: list[dict[str, Any]]) -> list[PanelZone]:
    return [PanelZone(z["number"], z["name"], z.get("type"), list(z.get("areas", []))) for z in data]


def areas_from_data(data: list[dict[str, Any]]) -> list[PanelArea]:
    return [PanelArea(a["number"], a["name"]) for a in data]


def layout_to_data(info: PanelInfo, zones: list[PanelZone], areas: list[PanelArea]) -> dict[str, Any]:
    return {
        CONF_INFO: asdict(info),
        CONF_ZONES: [{"number": z.number, "name": z.name, "type": z.panel_type, "areas": z.areas} for z in zones],
        CONF_AREAS: [{"number": a.number, "name": a.name} for a in areas],
    }


def create_panel(hass: HomeAssistant, entry: ConfigEntry) -> TexecomPanel:
    data, options = entry.data, entry.options
    part_arms = {"home": options.get(CONF_HOME_PART_ARM, 0), "night": options.get(CONF_NIGHT_PART_ARM, 0)}

    @callback
    def fire(kind: str, details: dict[str, Any]) -> None:
        hass.bus.async_fire(EVENT, {"type": kind, "entry_id": entry.entry_id, **details})

    if data[CONF_PROTOCOL] == PROTOCOL_CONNECT:

        @callback
        def layout_changed(info: PanelInfo, zones: list[PanelZone], areas: list[PanelArea]) -> None:
            _LOGGER.info("The panel's zones or areas changed; reloading")
            hass.config_entries.async_update_entry(entry, data={**entry.data, **layout_to_data(info, zones, areas)})
            hass.config_entries.async_schedule_reload(entry.entry_id)

        @callback
        def auth_failed() -> None:
            entry.async_start_reauth(hass)

        return ConnectPanel(
            host=data[CONF_HOST],
            port=data[CONF_PORT],
            udl=data[CONF_UDL],
            part_arms=part_arms,
            info=PanelInfo(**data[CONF_INFO]),
            zones=zones_from_data(data[CONF_ZONES]),
            areas=areas_from_data(data[CONF_AREAS]),
            time_zone=ZoneInfo(hass.config.time_zone),
            time_sync_hours=TIME_SYNC_HOURS if options.get(CONF_TIME_SYNC) else 0,
            on_event=fire,
            on_layout_changed=layout_changed,
            on_auth_failed=auth_failed,
        )

    zones = [PanelZone(n, f"Zone {n}") for n in range(1, data[CONF_ZONE_COUNT] + 1)]
    areas = [PanelArea(n, f"Area {chr(64 + n)}") for n in range(1, data[CONF_AREA_COUNT] + 1)]
    serial = data.get(CONF_CONNECTION) == CONNECTION_SERIAL
    return CrestronPanel(
        part_arms=part_arms,
        zones=zones,
        areas=areas,
        udl=data.get(CONF_UDL),
        host=None if serial else data[CONF_HOST],
        port=None if serial else data[CONF_PORT],
        serial_device=data.get(CONF_SERIAL_DEVICE) if serial else None,
        baud_rate=data.get(CONF_BAUD_RATE, DEFAULT_BAUD_RATE),
        keypad_arm_mode=options.get(CONF_KEYPAD_ARM_MODE, "away"),
        status_poll=options.get(CONF_STATUS_POLL, DEFAULT_STATUS_POLL),
        on_event=fire,
    )


async def async_setup_entry(hass: HomeAssistant, entry: TexecomConfigEntry) -> bool:
    panel = create_panel(hass, entry)
    entry.runtime_data = panel
    # The panel device exists before the area and zone devices that link to it.
    dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, **panel_device_info(entry, panel))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Connects in the background: entities show as unavailable until then.
    await panel.start()
    if entry.data.get(CONF_CREATE_DASHBOARD):
        # Asked for at the end of setup; done once the entities exist.
        with contextlib.suppress(HomeAssistantError):
            await async_create_dashboard(hass, entry)
        data = {k: v for k, v in entry.data.items() if k != CONF_CREATE_DASHBOARD}
        hass.config_entries.async_update_entry(entry, data=data)
    options = dict(entry.options)

    async def options_updated(hass: HomeAssistant, entry: TexecomConfigEntry) -> None:
        if dict(entry.options) != options:  # data-only updates don't need a reload
            await hass.config_entries.async_reload(entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(options_updated))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TexecomConfigEntry) -> bool:
    await entry.runtime_data.stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
