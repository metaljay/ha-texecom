"""Setting up over Crestron: a COM port set to "Crestron System", reached
over the network (an adapter, or a SmartCom in Crestron mode) or a serial
cable. Crestron can't read names, so the zone and area counts are asked for."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from ..const import (
    CONF_AREA_COUNT,
    CONF_BAUD_RATE,
    CONF_CONNECTION,
    CONF_PROTOCOL,
    CONF_SERIAL_DEVICE,
    CONF_UDL,
    CONF_ZONE_COUNT,
    CONNECTION_NETWORK,
    CONNECTION_SERIAL,
    DEFAULT_BAUD_RATE,
    DEFAULT_CRESTRON_PORT,
    HELP_CRESTRON,
    PROTOCOL_CRESTRON,
)
from .validation import PORT_SELECTOR, UDL_SELECTOR, udl_valid, validate_crestron_network, validate_serial


class CrestronSteps(ConfigFlow):
    """Part of TexecomConfigFlow: the Crestron setup screens. They go on to
    async_step_arm_modes."""

    _data: dict[str, Any]  # set up in TexecomConfigFlow.__init__

    async def async_step_crestron(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="crestron", menu_options=["crestron_network", "crestron_serial"])

    def _crestron_common(self, defaults: Mapping[str, Any]) -> dict[Any, Any]:
        return {
            vol.Optional(CONF_UDL, description={"suggested_value": defaults.get(CONF_UDL)}): UDL_SELECTOR,
            vol.Required(CONF_ZONE_COUNT, default=defaults.get(CONF_ZONE_COUNT, 8)): NumberSelector(
                NumberSelectorConfig(min=1, max=168, mode=NumberSelectorMode.BOX)
            ),
            vol.Required(CONF_AREA_COUNT, default=defaults.get(CONF_AREA_COUNT, 1)): NumberSelector(
                NumberSelectorConfig(min=1, max=8, mode=NumberSelectorMode.BOX)
            ),
        }

    def _crestron_counts(self, user_input: Mapping[str, Any]) -> dict[str, Any]:
        return {
            CONF_UDL: (user_input.get(CONF_UDL) or "").strip() or None,
            CONF_ZONE_COUNT: int(user_input[CONF_ZONE_COUNT]),
            CONF_AREA_COUNT: int(user_input[CONF_AREA_COUNT]),
        }

    async def async_step_crestron_network(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host, port = user_input[CONF_HOST].strip(), int(user_input[CONF_PORT])
            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()
            udl = (user_input.get(CONF_UDL) or "").strip()
            if udl and not udl_valid(udl):
                errors[CONF_UDL] = "invalid_udl"
            elif error := await validate_crestron_network(host, port):
                errors["base"] = error
            else:
                self._data = {
                    CONF_PROTOCOL: PROTOCOL_CRESTRON,
                    CONF_CONNECTION: CONNECTION_NETWORK,
                    CONF_HOST: host,
                    CONF_PORT: port,
                    **self._crestron_counts(user_input),
                }
                return await self.async_step_arm_modes()
        defaults = user_input or {}
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
                vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_CRESTRON_PORT)): PORT_SELECTOR,
                **self._crestron_common(defaults),
            }
        )
        return self.async_show_form(
            step_id="crestron_network",
            data_schema=schema,
            errors=errors,
            description_placeholders={"help": HELP_CRESTRON},
        )

    async def async_step_crestron_serial(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            device = user_input[CONF_SERIAL_DEVICE].strip()
            await self.async_set_unique_id(f"serial:{device}")
            self._abort_if_unique_id_configured()
            udl = (user_input.get(CONF_UDL) or "").strip()
            if udl and not udl_valid(udl):
                errors[CONF_UDL] = "invalid_udl"
            elif error := await validate_serial(device, int(user_input[CONF_BAUD_RATE])):
                errors["base"] = error
            else:
                self._data = {
                    CONF_PROTOCOL: PROTOCOL_CRESTRON,
                    CONF_CONNECTION: CONNECTION_SERIAL,
                    CONF_SERIAL_DEVICE: device,
                    CONF_BAUD_RATE: int(user_input[CONF_BAUD_RATE]),
                    **self._crestron_counts(user_input),
                }
                return await self.async_step_arm_modes()
        defaults = user_input or {}
        schema = vol.Schema(
            {
                vol.Required(CONF_SERIAL_DEVICE, default=defaults.get(CONF_SERIAL_DEVICE, "/dev/ttyUSB0")): str,
                vol.Required(CONF_BAUD_RATE, default=defaults.get(CONF_BAUD_RATE, DEFAULT_BAUD_RATE)): SelectSelector(
                    SelectSelectorConfig(
                        options=["9600", "19200", "38400", "57600", "115200"], mode=SelectSelectorMode.DROPDOWN
                    )
                ),
                **self._crestron_common(defaults),
            }
        )
        return self.async_show_form(
            step_id="crestron_serial",
            data_schema=schema,
            errors=errors,
            description_placeholders={"help": HELP_CRESTRON},
        )
