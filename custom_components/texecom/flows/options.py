"""The integration's options: the arm modes, an optional Home Assistant alarm
code, clock sync (Connect) or how often to check the panel (Crestron),
reading zones and areas again, and rebuilding the Alarm dashboard."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlowResult, OptionsFlow
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import BooleanSelector, NumberSelector, NumberSelectorConfig, NumberSelectorMode

from ..const import (
    CONF_ALARM_CODE,
    CONF_CODE_ARM_REQUIRED,
    CONF_CREATE_DASHBOARD,
    CONF_PROTOCOL,
    CONF_REDISCOVER,
    CONF_STATUS_POLL,
    CONF_TIME_SYNC,
    DEFAULT_STATUS_POLL,
    PROTOCOL_CONNECT,
)
from ..dashboard import async_create_dashboard
from ..factory import layout_to_data
from ..panel import PanelError
from .validation import UDL_SELECTOR, arm_mode_options, arm_modes_error, arm_modes_schema

_LOGGER = logging.getLogger(__name__)


class TexecomOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self.config_entry
        is_connect = entry.data[CONF_PROTOCOL] == PROTOCOL_CONNECT
        errors: dict[str, str] = {}
        reload_for_layout = False
        if user_input is not None:
            code = (user_input.get(CONF_ALARM_CODE) or "").strip()
            if error := arm_modes_error(user_input):
                errors["base"] = error
            elif code and not code.isdigit():
                errors[CONF_ALARM_CODE] = "invalid_code_format"
            else:
                options = arm_mode_options(user_input)
                options[CONF_ALARM_CODE] = code or None
                options[CONF_CODE_ARM_REQUIRED] = bool(user_input.get(CONF_CODE_ARM_REQUIRED))
                if is_connect:
                    options[CONF_TIME_SYNC] = bool(user_input.get(CONF_TIME_SYNC))
                    if user_input.get(CONF_REDISCOVER):
                        # Over the open session: the panel allows only one.
                        try:
                            info, zones, areas = await entry.runtime_data.async_rediscover()
                        except (PanelError, AttributeError) as err:
                            _LOGGER.debug("Re-reading the panel failed: %s", err)
                            errors["base"] = "rediscover_failed"
                        else:
                            self.hass.config_entries.async_update_entry(
                                entry, data={**entry.data, **layout_to_data(info, zones, areas)}
                            )
                            reload_for_layout = True
                else:
                    options[CONF_STATUS_POLL] = int(user_input[CONF_STATUS_POLL])
                if not errors and user_input.get(CONF_CREATE_DASHBOARD):
                    try:
                        await async_create_dashboard(self.hass, entry)
                    except HomeAssistantError:
                        errors["base"] = "dashboard_failed"
                if not errors:
                    if reload_for_layout and options == dict(entry.options):
                        # Changed options reload the entry anyway; two reloads
                        # in a row would log in twice, which a SmartCom refuses.
                        self.hass.config_entries.async_schedule_reload(entry.entry_id)
                    return self.async_create_entry(data=options)

        current = {**entry.options, **(user_input or {})}
        schema = arm_modes_schema(current, crestron=not is_connect).schema
        schema = {
            **schema,
            vol.Optional(CONF_ALARM_CODE, description={"suggested_value": current.get(CONF_ALARM_CODE)}): UDL_SELECTOR,
            vol.Required(CONF_CODE_ARM_REQUIRED, default=current.get(CONF_CODE_ARM_REQUIRED, False)): BooleanSelector(),
            vol.Required(CONF_CREATE_DASHBOARD, default=False): BooleanSelector(),
        }
        if is_connect:
            schema[vol.Required(CONF_TIME_SYNC, default=current.get(CONF_TIME_SYNC, False))] = BooleanSelector()
            schema[vol.Required(CONF_REDISCOVER, default=False)] = BooleanSelector()
        else:
            schema[vol.Required(CONF_STATUS_POLL, default=current.get(CONF_STATUS_POLL, DEFAULT_STATUS_POLL))] = (
                NumberSelector(
                    NumberSelectorConfig(min=0, max=600, step=10, mode=NumberSelectorMode.BOX, unit_of_measurement="s")
                )
            )
        return self.async_show_form(
            step_id="init" if is_connect else "init_crestron",
            data_schema=vol.Schema(schema),
            errors=errors,
        )

    async_step_init_crestron = async_step_init
