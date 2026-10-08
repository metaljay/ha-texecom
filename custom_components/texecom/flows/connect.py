"""Setting up over Texecom Connect (a SmartCom or ComIP): the address and
the UDL code. The panel's zones and areas are then read from it behind a
"Connecting to your panel…" screen, because a busy SmartCom can take up to a
minute to let Home Assistant in."""

from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT

from ..const import CONF_PROTOCOL, CONF_UDL, DEFAULT_CONNECT_PORT, DEFAULT_UDL, HELP_SETUP, PROTOCOL_CONNECT
from .validation import PORT_SELECTOR, UDL_SELECTOR, udl_valid, validate_connect


class ConnectSteps(ConfigFlow):
    """Part of TexecomConfigFlow: the Texecom Connect setup screen, and the
    progress screen while it logs in. It goes on to async_step_arm_modes."""

    _data: dict[str, Any]  # set up in TexecomConfigFlow.__init__
    _connect_input: dict[str, Any] | None = None  # what was entered, while it's checked
    _connect_task: asyncio.Task | None = None
    _connect_error: str | None = None  # why the check failed, for the form

    async def async_step_connect(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host, port, udl = user_input[CONF_HOST].strip(), int(user_input[CONF_PORT]), user_input[CONF_UDL].strip()
            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()
            if not udl_valid(udl):
                errors[CONF_UDL] = "invalid_udl"
            else:
                self._connect_input = {CONF_HOST: host, CONF_PORT: port, CONF_UDL: udl}
                return await self.async_step_connect_check()
        elif self._connect_error:
            # Back from the check: say why, and keep what was entered.
            errors["base"], self._connect_error = self._connect_error, None
            user_input = self._connect_input
        defaults = user_input or {}
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
                vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_CONNECT_PORT)): PORT_SELECTOR,
                vol.Required(CONF_UDL, default=defaults.get(CONF_UDL, DEFAULT_UDL)): UDL_SELECTOR,
            }
        )
        return self.async_show_form(
            step_id="connect", data_schema=schema, errors=errors, description_placeholders={"help": HELP_SETUP}
        )

    async def async_step_connect_check(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Logs in and reads the zones and areas, showing progress meanwhile."""
        if self._connect_task is None:
            entered = self._connect_input or {}
            self._connect_task = self.hass.async_create_task(
                validate_connect(entered[CONF_HOST], entered[CONF_PORT], entered[CONF_UDL])
            )
        if not self._connect_task.done():
            return self.async_show_progress(
                step_id="connect_check", progress_action="connecting", progress_task=self._connect_task
            )
        layout, error = self._connect_task.result()
        self._connect_task = None
        if error:
            self._connect_error = error
            return self.async_show_progress_done(next_step_id="connect")
        self._data = {CONF_PROTOCOL: PROTOCOL_CONNECT, **(self._connect_input or {}), **(layout or {})}
        return self.async_show_progress_done(next_step_id="arm_modes")
