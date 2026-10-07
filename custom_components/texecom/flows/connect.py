"""Setting up over Texecom Connect (a SmartCom or ComIP): the address and
the UDL code. The panel's zones and areas are read from it."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT

from ..const import CONF_PROTOCOL, CONF_UDL, DEFAULT_CONNECT_PORT, DEFAULT_UDL, PROTOCOL_CONNECT
from .validation import PORT_SELECTOR, UDL_SELECTOR, udl_valid, validate_connect


class ConnectSteps(ConfigFlow):
    """Part of TexecomConfigFlow: the Texecom Connect setup screen. It goes
    on to async_step_arm_modes."""

    _data: dict[str, Any]  # set up in TexecomConfigFlow.__init__

    async def async_step_connect(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host, port, udl = user_input[CONF_HOST].strip(), int(user_input[CONF_PORT]), user_input[CONF_UDL].strip()
            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()
            if not udl_valid(udl):
                errors[CONF_UDL] = "invalid_udl"
            else:
                layout, error = await validate_connect(host, port, udl)
                if error:
                    errors["base"] = error
                else:
                    self._data = {
                        CONF_PROTOCOL: PROTOCOL_CONNECT,
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_UDL: udl,
                        **layout,
                    }
                    return await self.async_step_arm_modes()
        defaults = user_input or {}
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
                vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_CONNECT_PORT)): PORT_SELECTOR,
                vol.Required(CONF_UDL, default=defaults.get(CONF_UDL, DEFAULT_UDL)): UDL_SELECTOR,
            }
        )
        return self.async_show_form(step_id="connect", data_schema=schema, errors=errors)
