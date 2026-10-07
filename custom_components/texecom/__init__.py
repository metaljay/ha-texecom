"""The Texecom integration: Premier Elite panels over Texecom Connect
(SmartCom/ComIP) or a COM port set to Crestron.

Setting up and unloading a config entry. The panel driver is built in
factory.py; the notices it raises are in notifications.py and issues.py."""

from __future__ import annotations

import contextlib

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr

from .const import CONF_CREATE_DASHBOARD, CONF_TIME_SYNC
from .dashboard import async_create_dashboard
from .entity import panel_device_info
from .factory import create_panel
from .issues import clear_clock_issue, watch_connection
from .notifications import dismiss_when_armed
from .panel import TexecomPanel

PLATFORMS = [Platform.ALARM_CONTROL_PANEL, Platform.BINARY_SENSOR, Platform.SENSOR]

type TexecomConfigEntry = ConfigEntry[TexecomPanel]


async def async_setup_entry(hass: HomeAssistant, entry: TexecomConfigEntry) -> bool:
    panel = create_panel(hass, entry)
    entry.runtime_data = panel
    # The panel device exists before the area and zone devices that link to it.
    dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, **panel_device_info(entry, panel))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # Connects in the background: entities show as unavailable until then.
    await panel.start()
    if entry.options.get(CONF_TIME_SYNC):
        clear_clock_issue(hass, entry)
    entry.async_on_unload(watch_connection(hass, entry, panel))
    entry.async_on_unload(dismiss_when_armed(hass, entry, panel))
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
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.stop()
    return unloaded
