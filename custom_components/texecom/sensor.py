"""Power readings (Texecom Connect only)."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfElectricCurrent, UnitOfElectricPotential
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TexecomConfigEntry
from .const import CONF_PROTOCOL, PROTOCOL_CONNECT
from .entity import TexecomEntity
from .panel import TexecomPanel

# (key, attribute of SystemPower, device class, unit, enabled by default)
POWER_SENSORS = (
    ("panel_voltage", "panel_voltage", SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT, True),
    ("battery_voltage", "battery_voltage", SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT, True),
    ("panel_current", "panel_current", SensorDeviceClass.CURRENT, UnitOfElectricCurrent.MILLIAMPERE, False),
    ("battery_current", "battery_current", SensorDeviceClass.CURRENT, UnitOfElectricCurrent.MILLIAMPERE, False),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: TexecomConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    if entry.data[CONF_PROTOCOL] != PROTOCOL_CONNECT:
        return
    panel = entry.runtime_data
    async_add_entities(TexecomPowerSensor(entry, panel, *spec) for spec in POWER_SENSORS)


class TexecomPowerSensor(TexecomEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        entry: TexecomConfigEntry,
        panel: TexecomPanel,
        key: str,
        attribute: str,
        device_class: SensorDeviceClass,
        unit: str,
        enabled: bool,
    ) -> None:
        super().__init__(entry, panel, key)
        self._attribute = attribute
        self._attr_translation_key = key
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_entity_registry_enabled_default = enabled
        if device_class == SensorDeviceClass.VOLTAGE:
            self._attr_suggested_display_precision = 2

    @property
    def available(self) -> bool:
        return self.panel.connected and "power" in self.panel.extra

    @property
    def native_value(self) -> float | None:
        power = self.panel.extra.get("power")
        return getattr(power, self._attribute) if power else None
