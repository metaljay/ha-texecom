"""The Texecom integration: Premier Elite panels over Texecom Connect
(SmartCom/ComIP) or a COM port set to Crestron.

Setting up and unloading a config entry. The panel driver is built in
factory.py; the notices it raises are in notifications.py and issues.py.
Changing an option the driver uses reloads the entry; the others apply at
once. The names for keypad users are filled in from the panel when it first
connects (users.py). Entries made before 0.2.11 get its defaults once
(async_migrate_entry). A zone or area the panel no longer has can have its
device deleted."""

from __future__ import annotations

import contextlib

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import CONF_CREATE_DASHBOARD, CONF_TIME_SYNC, DOMAIN, DRIVER_OPTIONS
from .dashboard import async_create_dashboard, async_remove_dashboard, async_update_alarm_buttons
from .entity import panel_device_info
from .factory import create_panel
from .issues import clear_clock_issue, watch_connection, watch_unrecognised
from .notifications import dismiss_when_armed, watch_conditions
from .panel import TexecomPanel
from .users import fill_in_names_once

PLATFORMS = [Platform.ALARM_CONTROL_PANEL, Platform.BINARY_SENSOR, Platform.SENSOR]

type TexecomConfigEntry = ConfigEntry[TexecomPanel]


async def async_migrate_entry(hass: HomeAssistant, entry: TexecomConfigEntry) -> bool:
    """Gives older entries, once, what new setups start with, where the user
    hasn't chosen otherwise (while in beta, new defaults reach existing users).
    2 (0.2.11): the sensors that used to be off by default (zone tampers,
    currents). (0.2.11 to 0.2.15 also switched on Keep the panel clock right;
    it writes to the panel, so it's now only ever the user's choice, and
    entries already upgraded keep what they have.) Switching entities on
    makes Home Assistant reload the entry once, about half a minute later.
    3 (0.2.12): the Alarm dashboard's buttons follow Configure → Arm buttons."""
    if entry.version == 1 and entry.minor_version < 2:
        registry = er.async_get(hass)
        for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
            if entity.disabled_by is er.RegistryEntryDisabler.INTEGRATION:
                registry.async_update_entity(entity.entity_id, disabled_by=None)
        hass.config_entries.async_update_entry(entry, minor_version=2)
    if entry.version == 1 and entry.minor_version < 3:
        await async_update_alarm_buttons(hass, entry)
        hass.config_entries.async_update_entry(entry, minor_version=3)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: TexecomConfigEntry) -> bool:
    panel = create_panel(hass, entry)
    entry.runtime_data = panel
    # The panel device exists before the area and zone devices that link to it.
    dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, **panel_device_info(entry, panel))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if entry.options.get(CONF_TIME_SYNC):
        clear_clock_issue(hass, entry)
    entry.async_on_unload(watch_connection(hass, entry, panel))
    entry.async_on_unload(watch_unrecognised(hass, entry, panel))
    entry.async_on_unload(dismiss_when_armed(hass, entry, panel))
    entry.async_on_unload(watch_conditions(hass, entry, panel))
    entry.async_on_unload(fill_in_names_once(hass, entry, panel))
    if entry.data.get(CONF_CREATE_DASHBOARD):
        # Asked for at the end of setup; done once the entities exist.
        with contextlib.suppress(HomeAssistantError):
            await async_create_dashboard(hass, entry)
        data = {k: v for k, v in entry.data.items() if k != CONF_CREATE_DASHBOARD}
        hass.config_entries.async_update_entry(entry, data=data)
    driver_options = {key: entry.options.get(key) for key in DRIVER_OPTIONS}

    async def options_updated(hass: HomeAssistant, entry: TexecomConfigEntry) -> None:
        # Reconnecting costs up to a minute (the SmartCom is slow to let a new
        # session in), so only when the driver uses what changed. Codes, names
        # and notifications apply straight away.
        if {key: entry.options.get(key) for key in DRIVER_OPTIONS} != driver_options:
            await hass.config_entries.async_reload(entry.entry_id)
        else:
            panel.notify()

    entry.async_on_unload(entry.add_update_listener(options_updated))

    async def home_assistant_stopping(_event: Event) -> None:
        # Home Assistant doesn't unload integrations when it stops: close the
        # session and stop reconnecting, rather than be cut off at the end.
        await panel.stop()

    entry.async_on_unload(hass.bus.async_listen(EVENT_HOMEASSISTANT_STOP, home_assistant_stopping))
    # Connects in the background (entities show as unavailable until then),
    # last: if anything above fails, no connection is left holding the
    # SmartCom's only session.
    await panel.start()
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: TexecomConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Whether a device may be deleted (its ⋮ → Delete): only a zone or area
    the panel no longer has, e.g. after the installer removed a zone."""
    panel = getattr(entry, "runtime_data", None)
    if panel is None:
        return False
    current = {entry.entry_id}
    current |= {f"{entry.entry_id}_zone_{number}" for number in panel.zones}
    current |= {f"{entry.entry_id}_area_{number}" for number in panel.areas}
    return not any(domain == DOMAIN and key in current for domain, key in device.identifiers)


async def async_remove_entry(hass: HomeAssistant, entry: TexecomConfigEntry) -> None:
    """The panel was deleted: delete its Alarm dashboard too."""
    await async_remove_dashboard(hass, entry)


async def async_unload_entry(hass: HomeAssistant, entry: TexecomConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.stop()
    return unloaded
