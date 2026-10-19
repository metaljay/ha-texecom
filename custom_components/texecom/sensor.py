"""Power readings (Texecom Connect, and Crestron with the UDL code and readings on) and the keypad text (Connect)."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfElectricCurrent, UnitOfElectricPotential
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TexecomConfigEntry
from .const import CONF_PROTOCOL, CONF_SESSION_READS, CONF_UDL, PROTOCOL_CONNECT
from .entity import TexecomEntity
from .panel import TexecomPanel

# (key, attribute of SystemPower, device class, unit)
POWER_SENSORS = (
    ("panel_voltage", "panel_voltage", SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT),
    ("battery_voltage", "battery_voltage", SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT),
    ("panel_current", "panel_current", SensorDeviceClass.CURRENT, UnitOfElectricCurrent.MILLIAMPERE),
    ("battery_current", "battery_current", SensorDeviceClass.CURRENT, UnitOfElectricCurrent.MILLIAMPERE),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: TexecomConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    panel = entry.runtime_data
    power = [TexecomPowerSensor(entry, panel, *spec) for spec in POWER_SENSORS]
    if entry.data[CONF_PROTOCOL] == PROTOCOL_CONNECT:
        async_add_entities([*power, TexecomDisplaySensor(entry, panel)])
    elif entry.data.get(CONF_UDL) and entry.options.get(CONF_SESSION_READS):
        # Crestron reads power through Texecom Connect in a short UDL session
        # every 30 minutes: only with the UDL code and the readings switched on.
        async_add_entities(power)


class TexecomPowerSensor(TexecomEntity, SensorEntity):
    _entity_domain = "sensor"
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
    ) -> None:
        super().__init__(entry, panel, key, object_id=key)
        self._attribute = attribute
        self._attr_translation_key = key
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        if device_class == SensorDeviceClass.VOLTAGE:
            self._attr_suggested_display_precision = 2

    @property
    def available(self) -> bool:
        return self.panel.recently_connected and "power" in self.panel.extra

    @property
    def native_value(self) -> float | None:
        power = self.panel.extra.get("power")
        return getattr(power, self._attribute) if power else None


class TexecomDisplaySensor(TexecomEntity, SensorEntity):
    """The text on the keypads (read every 30 seconds, and a moment after an
    area change, a failed arm, a tamper or a fault)."""

    _entity_domain = "sensor"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "display"

    def __init__(self, entry: TexecomConfigEntry, panel: TexecomPanel) -> None:
        super().__init__(entry, panel, "display", object_id="keypad_display")

    @property
    def available(self) -> bool:
        return self.panel.recently_connected and "display" in self.panel.extra

    @property
    def native_value(self) -> str | None:
        return self.panel.extra.get("display")
