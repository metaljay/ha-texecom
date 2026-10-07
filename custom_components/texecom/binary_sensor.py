"""Zone sensors, zone tamper sensors, the panel's tamper, problem, mains and
connection sensors, and each area's "Ready to arm"."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TexecomConfigEntry
from .const import CONF_PROTOCOL, PROTOCOL_CONNECT
from .entity import TexecomEntity, child_device_info, nice_name
from .panel import MAINS_FAULTS, TexecomPanel

# Texecom zone types (as reported over Connect).
ZONE_TYPE_FIRE = 9
ZONE_TYPE_GAS = 11
ZONE_TYPES_PANIC = (7, 8, 20, 21)
ZONE_TYPE_TAMPER = 13

_OPENING = re.compile(r"\b(door|gate|shutter|patio)\b", re.IGNORECASE)
_WINDOW = re.compile(r"\b(window|windows)\b", re.IGNORECASE)
_GARAGE_DOOR = re.compile(r"\bgarage door\b", re.IGNORECASE)
_SHOCK = re.compile(r"\b(shock|vibration)\b", re.IGNORECASE)


def guess_device_class(name: str, panel_type: int | None) -> BinarySensorDeviceClass:
    """A sensible default; users can change it under the entity's "Show as"."""
    if panel_type == ZONE_TYPE_FIRE:
        return BinarySensorDeviceClass.SMOKE
    if panel_type == ZONE_TYPE_GAS:
        return BinarySensorDeviceClass.GAS
    if panel_type in ZONE_TYPES_PANIC:
        return BinarySensorDeviceClass.SAFETY
    if panel_type == ZONE_TYPE_TAMPER:
        return BinarySensorDeviceClass.TAMPER
    if _GARAGE_DOOR.search(name):
        return BinarySensorDeviceClass.GARAGE_DOOR
    if _WINDOW.search(name):
        return BinarySensorDeviceClass.WINDOW
    if _OPENING.search(name):
        return BinarySensorDeviceClass.DOOR
    if _SHOCK.search(name):
        return BinarySensorDeviceClass.VIBRATION
    return BinarySensorDeviceClass.MOTION


async def async_setup_entry(
    hass: HomeAssistant, entry: TexecomConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    panel = entry.runtime_data
    entities: list[BinarySensorEntity] = [TexecomConnectionSensor(entry, panel)]
    is_connect = entry.data[CONF_PROTOCOL] == PROTOCOL_CONNECT
    if is_connect:
        entities += [
            TexecomSystemTamperSensor(entry, panel),
            TexecomProblemSensor(entry, panel),
            TexecomMainsSensor(entry, panel),
        ]
    if panel.reports_ready:
        for number, area in panel.areas.items():
            # The same device as the area's alarm (alarm_control_panel.py).
            device = child_device_info(
                hass, entry, f"area_{number}", f"{nice_name(area.name)} Alarm", f"Alarm area {number}"
            )
            entities.append(TexecomReadySensor(entry, panel, number, device))
    for number, zone in panel.zones.items():
        # Crestron zones are only "Zone 1"… until renamed, so no room match.
        device = child_device_info(
            hass, entry, f"zone_{number}", zone.name, f"Zone {number}", zone.name if is_connect else None
        )
        entities.append(TexecomZoneSensor(entry, panel, number, device))
        if is_connect:  # Crestron only reports active/clear
            entities.append(TexecomZoneTamperSensor(entry, panel, number, device))
    async_add_entities(entities)


class TexecomBinarySensor(TexecomEntity, BinarySensorEntity):
    _entity_domain = "binary_sensor"


class TexecomZoneSensor(TexecomBinarySensor):
    _attr_name = None  # the zone's device name, e.g. "Front Door"

    def __init__(self, entry: TexecomConfigEntry, panel: TexecomPanel, number: int, device: DeviceInfo) -> None:
        super().__init__(entry, panel, f"zone_{number}", device, panel.zones[number].name)
        zone = panel.zones[number]
        self.number = number
        self._attr_device_class = guess_device_class(zone.name, zone.panel_type)

    @property
    def is_on(self) -> bool:
        return self.panel.zones[self.number].active

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        zone = self.panel.zones[self.number]
        attrs: dict[str, Any] = {"zone_number": self.number, "zone_state": zone.state}
        if zone.areas:
            attrs["areas"] = zone.areas
        if zone.bypassed:
            attrs["bypassed"] = True
        return attrs


class TexecomZoneTamperSensor(TexecomBinarySensor):
    _attr_device_class = BinarySensorDeviceClass.TAMPER
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "zone_tamper"

    def __init__(self, entry: TexecomConfigEntry, panel: TexecomPanel, number: int, device: DeviceInfo) -> None:
        super().__init__(entry, panel, f"zone_{number}_tamper", device, f"{panel.zones[number].name} tamper")
        self.number = number

    @property
    def is_on(self) -> bool:
        return self.panel.zones[self.number].tampered


class TexecomSystemTamperSensor(TexecomBinarySensor):
    """On while the panel reports a tamper that isn't a zone: its lid, the
    shared detector tamper circuit, a keypad, the bell box..."""

    _attr_device_class = BinarySensorDeviceClass.TAMPER
    _attr_translation_key = "system_tamper"

    def __init__(self, entry: TexecomConfigEntry, panel: TexecomPanel) -> None:
        super().__init__(entry, panel, "system_tamper", object_id="tamper")

    @property
    def is_on(self) -> bool:
        return bool(self.panel.extra.get("tampers"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"sources": sorted(self.panel.extra.get("tampers", ()))}


class TexecomProblemSensor(TexecomBinarySensor):
    """On while the panel reports a fault: mains, battery, communication..."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "problem"

    def __init__(self, entry: TexecomConfigEntry, panel: TexecomPanel) -> None:
        super().__init__(entry, panel, "problem", object_id="problem")

    @property
    def is_on(self) -> bool:
        return bool(self.panel.extra.get("faults"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"sources": sorted(self.panel.extra.get("faults", ()))}


class TexecomMainsSensor(TexecomBinarySensor):
    """Mains power to the panel (off while it runs on its battery)."""

    _attr_device_class = BinarySensorDeviceClass.POWER
    _attr_translation_key = "mains"

    def __init__(self, entry: TexecomConfigEntry, panel: TexecomPanel) -> None:
        super().__init__(entry, panel, "mains", object_id="mains_power")

    @property
    def is_on(self) -> bool:
        return not (self.panel.extra.get("faults", set()) & MAINS_FAULTS)


class TexecomReadySensor(TexecomBinarySensor):
    """Whether the panel would let this area arm now (nothing open that
    stops it), with the zones that are open."""

    _attr_translation_key = "ready_to_arm"

    def __init__(self, entry: TexecomConfigEntry, panel: TexecomPanel, number: int, device: DeviceInfo) -> None:
        super().__init__(entry, panel, f"area_{number}_ready", device, f"{panel.areas[number].name} ready to arm")
        self.number = number

    @property
    def available(self) -> bool:
        return super().available and self.panel.areas[self.number].ready is not None

    @property
    def is_on(self) -> bool:
        return bool(self.panel.areas[self.number].ready)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        open_zones = [
            nice_name(zone.name)
            for zone in self.panel.zones.values()
            if zone.state != "secure" and (not zone.areas or self.number in zone.areas)
        ]
        return {"open_zones": open_zones}


class TexecomConnectionSensor(TexecomBinarySensor):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "connection"

    def __init__(self, entry: TexecomConfigEntry, panel: TexecomPanel) -> None:
        super().__init__(entry, panel, "connection", object_id="panel_connection")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.panel.connected
