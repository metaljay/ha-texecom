"""Reauth (a new UDL code when the panel refuses the stored one) and
reconfigure (a new address, serial device or UDL code). The panel allows one
session, so reconfigure pauses the entry's own connection while it checks."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigEntryState, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT

from ..const import (
    CONF_BAUD_RATE,
    CONF_CONNECTION,
    CONF_PROTOCOL,
    CONF_SERIAL_DEVICE,
    CONF_UDL,
    CONNECTION_SERIAL,
    DEFAULT_BAUD_RATE,
    PROTOCOL_CONNECT,
)
from .validation import (
    PORT_SELECTOR,
    UDL_SELECTOR,
    udl_valid,
    validate_connect,
    validate_crestron_network,
    validate_serial,
)


class ReauthReconfigureSteps(ConfigFlow):
    """Part of TexecomConfigFlow: reauth and reconfigure."""

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            udl = user_input[CONF_UDL].strip()
            if not udl_valid(udl):
                errors[CONF_UDL] = "invalid_udl"
            else:
                _layout, error = await validate_connect(entry.data[CONF_HOST], entry.data[CONF_PORT], udl)
                if error:
                    errors["base"] = error
                else:
                    return self.async_update_reload_and_abort(entry, data_updates={CONF_UDL: udl})
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=vol.Schema({vol.Required(CONF_UDL): UDL_SELECTOR}), errors=errors
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        data = entry.data
        errors: dict[str, str] = {}
        is_connect = data[CONF_PROTOCOL] == PROTOCOL_CONNECT
        is_serial = data.get(CONF_CONNECTION) == CONNECTION_SERIAL
        if user_input is not None:
            udl = (user_input.get(CONF_UDL) or "").strip()
            updates: dict[str, Any] = {CONF_UDL: udl or None}
            if is_serial:
                updates[CONF_SERIAL_DEVICE] = user_input[CONF_SERIAL_DEVICE].strip()
                unique_id = f"serial:{updates[CONF_SERIAL_DEVICE]}"
            else:
                updates[CONF_HOST] = user_input[CONF_HOST].strip()
                updates[CONF_PORT] = int(user_input[CONF_PORT])
                unique_id = f"{updates[CONF_HOST]}:{updates[CONF_PORT]}"
            others = [e for e in self._async_current_entries(include_ignore=False) if e.entry_id != entry.entry_id]
            if any(e.unique_id == unique_id for e in others):
                return self.async_abort(reason="already_configured")
            if (udl or is_connect) and not udl_valid(udl):
                errors[CONF_UDL] = "invalid_udl"
            else:
                # The panel allows one session: pause ours while checking.
                await self._pause(entry)
                if is_connect:
                    layout, error = await validate_connect(updates[CONF_HOST], updates[CONF_PORT], udl)
                    if error:
                        errors["base"] = error
                    else:
                        updates.update(layout)
                elif is_serial:
                    if error := await validate_serial(
                        updates[CONF_SERIAL_DEVICE], data.get(CONF_BAUD_RATE, DEFAULT_BAUD_RATE)
                    ):
                        errors["base"] = error
                elif error := await validate_crestron_network(updates[CONF_HOST], updates[CONF_PORT]):
                    errors["base"] = error
                if errors:
                    await self.hass.config_entries.async_reload(entry.entry_id)  # carry on as before
            if not errors:
                return self.async_update_reload_and_abort(entry, unique_id=unique_id, data_updates=updates)
        defaults = {**data, **(user_input or {})}
        fields: dict[Any, Any] = {}
        if is_serial:
            fields[vol.Required(CONF_SERIAL_DEVICE, default=defaults.get(CONF_SERIAL_DEVICE, ""))] = str
        else:
            fields[vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, ""))] = str
            fields[vol.Required(CONF_PORT, default=defaults.get(CONF_PORT))] = PORT_SELECTOR
        udl_key = vol.Required(CONF_UDL) if is_connect else vol.Optional(CONF_UDL)
        fields[udl_key] = UDL_SELECTOR
        return self.async_show_form(
            step_id="reconfigure_connect" if is_connect else "reconfigure",
            data_schema=vol.Schema(fields),
            errors=errors,
        )

    async_step_reconfigure_connect = async_step_reconfigure

    async def _pause(self, entry: ConfigEntry) -> None:
        if entry.state is ConfigEntryState.LOADED:
            await self.hass.config_entries.async_unload(entry.entry_id)
