"""The Texecom integration: Premier Elite panels over Texecom Connect
(SmartCom/ComIP) or a COM port set to Crestron."""

from __future__ import annotations

import contextlib
import logging
import time
from dataclasses import asdict
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval

from .connect.panel import ConnectPanel
from .const import (
    CLOCK_DRIFT_LIMIT,
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
    DOMAIN,
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


OFFLINE_ISSUE_AFTER = 15 * 60  # seconds without a connection before Repairs says so
ARM_FAILED_WINDOW = 60.0  # zones reported within this of each other are one failed arm


def describe_drift(drift: int | None) -> str:
    """E.g. "about 3 years behind", "12 minutes ahead"; None: an impossible date."""
    if drift is None:
        return "not set (it holds an impossible date)"
    seconds = abs(drift)
    for unit, size in (("year", 365 * 86400), ("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            count = round(seconds / size)
            amount = f"{count} {unit}{'s' if count != 1 else ''}"
            break
    else:
        amount = f"{seconds} seconds"
    return f"about {amount} {'ahead' if drift > 0 else 'behind'}"


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

    failed_zones: list[str] = []
    failed_at = [0.0]

    @callback
    def fire(kind: str, details: dict[str, Any]) -> None:
        hass.bus.async_fire(EVENT, {"type": kind, "entry_id": entry.entry_id, **details})
        if kind == "arm_failed":
            # The panel logs one entry per zone that stopped the arm; gather
            # them into one notification.
            now = time.monotonic()
            if now - failed_at[0] > ARM_FAILED_WINDOW:
                failed_zones.clear()
            failed_at[0] = now
            name = details.get("zone_name") or f"zone {details.get('zone')}"
            if name not in failed_zones:
                failed_zones.append(name)
            persistent_notification.async_create(
                hass,
                f"The panel didn't arm: **{', '.join(failed_zones)}** "
                f"{'was' if len(failed_zones) == 1 else 'were'} active when the exit time ended "
                '(the panel sounds its "fail to set" warning). Close the door or keep out of the '
                "sensor's view, then arm again.",
                title="Alarm not set",
                notification_id=f"{DOMAIN}_arm_failed_{entry.entry_id}",
            )

    if data[CONF_PROTOCOL] == PROTOCOL_CONNECT:

        @callback
        def layout_changed(info: PanelInfo, zones: list[PanelZone], areas: list[PanelArea]) -> None:
            _LOGGER.info("The panel's zones or areas changed; reloading")
            hass.config_entries.async_update_entry(entry, data={**entry.data, **layout_to_data(info, zones, areas)})
            hass.config_entries.async_schedule_reload(entry.entry_id)

        @callback
        def auth_failed() -> None:
            entry.async_start_reauth(hass)

        @callback
        def clock_drift(drift: int | None) -> None:
            issue_id = f"panel_clock_{entry.entry_id}"
            if drift is not None and abs(drift) <= CLOCK_DRIFT_LIMIT:
                ir.async_delete_issue(hass, DOMAIN, issue_id)
                return
            ir.async_create_issue(
                hass,
                DOMAIN,
                issue_id,
                is_fixable=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key="panel_clock_wrong",
                translation_placeholders={"drift": describe_drift(drift)},
                data={"entry_id": entry.entry_id},
            )

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
            on_clock_drift=clock_drift,
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
    if entry.options.get(CONF_TIME_SYNC):
        ir.async_delete_issue(hass, DOMAIN, f"panel_clock_{entry.entry_id}")
    entry.async_on_unload(_watch_connection(hass, entry, panel))

    @callback
    def armed_now() -> None:
        if any(area.state.startswith("armed") for area in panel.areas.values()):
            persistent_notification.async_dismiss(hass, f"{DOMAIN}_arm_failed_{entry.entry_id}")

    entry.async_on_unload(panel.add_listener(armed_now))
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


def _watch_connection(hass: HomeAssistant, entry: ConfigEntry, panel: TexecomPanel):
    """Raises a Repairs notice when the panel has been unreachable for a while
    (e.g. the SmartCom's address changed), and clears it once connected."""
    issue_id = f"panel_offline_{entry.entry_id}"
    address = entry.data.get(CONF_SERIAL_DEVICE) or f"{entry.data.get(CONF_HOST)}:{entry.data.get(CONF_PORT)}"

    @callback
    def check(_now=None) -> None:
        since = panel.disconnected_since
        if panel.connected or since is None:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
            return
        minutes = (time.monotonic() - since) / 60
        if minutes * 60 < OFFLINE_ISSUE_AFTER:
            return
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="panel_offline",
            translation_placeholders={
                "name": entry.title,
                "address": address,
                "minutes": str(round(minutes)),
                "error": getattr(panel, "last_error", None) or "no reply",
            },
        )

    remove_timer = async_track_time_interval(hass, check, timedelta(minutes=1))
    remove_listener = panel.add_listener(lambda: panel.connected and check())

    def remove() -> None:
        remove_timer()
        remove_listener()
        ir.async_delete_issue(hass, DOMAIN, issue_id)

    return remove


async def async_unload_entry(hass: HomeAssistant, entry: TexecomConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.stop()
    return unloaded
