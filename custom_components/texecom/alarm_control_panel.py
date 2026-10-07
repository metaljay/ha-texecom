"""An alarm control panel for each Texecom area."""

from __future__ import annotations

from typing import Any

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
    CodeFormat,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import TexecomConfigEntry
from .const import CONF_ALARM_CODE, CONF_CODE_ARM_REQUIRED, DOMAIN
from .entity import TexecomEntity, child_device_info
from .panel import PanelError, TexecomPanel

FEATURE_FOR_MODE = {
    "away": AlarmControlPanelEntityFeature.ARM_AWAY,
    "home": AlarmControlPanelEntityFeature.ARM_HOME,
    "night": AlarmControlPanelEntityFeature.ARM_NIGHT,
}


async def async_setup_entry(
    hass: HomeAssistant, entry: TexecomConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    panel = entry.runtime_data
    async_add_entities(TexecomAreaPanel(hass, entry, panel, number) for number in panel.areas)


class TexecomAreaPanel(TexecomEntity, AlarmControlPanelEntity):
    _attr_name = None  # the area's device name, e.g. "House"
    _entity_domain = "alarm_control_panel"

    def __init__(self, hass: HomeAssistant, entry: TexecomConfigEntry, panel: TexecomPanel, number: int) -> None:
        name = panel.areas[number].name
        device = child_device_info(hass, entry, f"area_{number}", name, f"Area {number}")
        super().__init__(entry, panel, f"area_{number}", device, name)
        self.number = number
        features = AlarmControlPanelEntityFeature(0)
        for mode in panel.offered_modes:
            features |= FEATURE_FOR_MODE[mode]
        self._attr_supported_features = features
        self._code: str | None = entry.options.get(CONF_ALARM_CODE) or None
        self._attr_code_format = CodeFormat.NUMBER if self._code else None
        self._attr_code_arm_required = bool(self._code) and entry.options.get(CONF_CODE_ARM_REQUIRED, False)

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        area = self.panel.areas[self.number]
        return AlarmControlPanelState(area.state) if area.known else None

    @property
    def changed_by(self) -> str | None:
        return self.panel.areas[self.number].changed_by

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        area = self.panel.areas[self.number]
        return {"area_number": self.number, "part_arm": area.part_arm}

    def _check_code(self, code: str | None, arming: bool) -> None:
        if not self._code or (arming and not self._attr_code_arm_required):
            return
        if code != self._code:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="invalid_code")

    async def _run(self, request) -> None:
        try:
            await request
        except PanelError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(err)},
            ) from err

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        self._check_code(code, arming=False)
        await self._run(self.panel.disarm(self.number))

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        self._check_code(code, arming=True)
        await self._run(self.panel.arm(self.number, "away"))

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        self._check_code(code, arming=True)
        await self._run(self.panel.arm(self.number, "home"))

    async def async_alarm_arm_night(self, code: str | None = None) -> None:
        self._check_code(code, arming=True)
        await self._run(self.panel.arm(self.number, "night"))
