"""Base entity for the Texecom integration.

Devices: the panel itself (connection and power diagnostics), and one small
device per area and per zone, linked to the panel. Area and zone entities take
their device's name, so dashboards show "Front Door: Closed" rather than
"Texecom Premier Elite 24 Front Door", and each zone can be put in a room.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.util import slugify

from .const import CONF_PROTOCOL, DOMAIN, PROTOCOL_CONNECT
from .panel import TexecomPanel


def nice_name(name: str) -> str:
    """Panels store names in capitals ("HOUSE"); show them in title case."""
    return name.title() if name.isupper() else name


def panel_device_info(entry: ConfigEntry, panel: TexecomPanel) -> DeviceInfo:
    info = panel.info
    is_connect = entry.data[CONF_PROTOCOL] == PROTOCOL_CONNECT
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer="Texecom",
        model=f"{info.model} {info.zones}" if is_connect and info.zones else "Premier Elite",
        sw_version=info.firmware if is_connect else None,
        name=entry.title,
    )


def matching_area(hass: HomeAssistant, name: str) -> str | None:
    """An existing Home Assistant area this zone's name points to, e.g. zone
    "Kitchen" or "Kitchen PIR" -> area "Kitchen". Never creates an area."""
    lowered = name.lower()
    best: tuple[str, str] | None = None
    for area in ar.async_get(hass).async_list_areas():
        for candidate in (area.name, *area.aliases):  # aliases: e.g. "Lounge" for "Living Room"
            word = candidate.lower()
            if lowered == word or f" {word} " in f" {lowered} ":
                if best is None or len(candidate) > len(best[1]):
                    best = (area.name, candidate)
    return best[0] if best else None


class TexecomEntity(Entity):
    """Pushes state whenever the panel reports a change."""

    _entity_domain = ""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        entry: ConfigEntry,
        panel: TexecomPanel,
        key: str,
        device: DeviceInfo | None = None,
        object_id: str | None = None,
    ) -> None:
        self.panel = panel
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = device or panel_device_info(entry, panel)
        if object_id:
            # A predictable entity ID (e.g. binary_sensor.texecom_front_door)
            # rather than one built from the room and device names.
            self.entity_id = f"{self._entity_domain}.texecom_{slugify(object_id)}"

    @property
    def available(self) -> bool:
        return self.panel.connected

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.panel.add_listener(self.async_write_ha_state))


def child_device_info(
    hass: HomeAssistant, entry: ConfigEntry, key: str, name: str, model: str, room_hint: str | None = None
) -> DeviceInfo:
    info = DeviceInfo(
        identifiers={(DOMAIN, f"{entry.entry_id}_{key}")},
        manufacturer="Texecom",
        model=model,
        name=nice_name(name),
    )
    # Link to the panel device. Newer Home Assistant wants its device id
    # (via_device is deprecated from 2026.8); older versions only know via_device.
    if "via_device_id" in DeviceInfo.__annotations__:
        registry = dr.async_get(hass)
        if hasattr(registry, "async_get_device_by_identifier"):
            hub = registry.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
        else:
            hub = registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
        if hub:
            info["via_device_id"] = hub.id
    else:
        info["via_device"] = (DOMAIN, entry.entry_id)
    if room_hint and (area := matching_area(hass, room_hint)):
        info["suggested_area"] = area
    return info
