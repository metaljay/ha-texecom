"""Builds the panel driver for a config entry and wires its callbacks into
Home Assistant: texecom_event on the bus, reauth, reloading when the panel's
zones change, and the notices in notifications.py and issues.py. Also the
stored form of a Connect panel's layout (the config entry's info, zones and
areas)."""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback

from .connect.panel import ConnectPanel
from .const import (
    CONF_AREA_COUNT,
    CONF_AREAS,
    CONF_BAUD_RATE,
    CONF_CONNECTION,
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
from .issues import clock_drift_reporter
from .notifications import arm_failed_notifier
from .panel import PanelArea, PanelInfo, PanelZone, TexecomPanel

_LOGGER = logging.getLogger(__name__)


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

    arm_failed = arm_failed_notifier(hass, entry)

    @callback
    def fire(kind: str, details: dict[str, Any]) -> None:
        hass.bus.async_fire(EVENT, {"type": kind, "entry_id": entry.entry_id, **details})
        if kind == "arm_failed":
            arm_failed(details)

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
            on_clock_drift=clock_drift_reporter(hass, entry),
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
