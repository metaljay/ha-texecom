"""Config flow for the Texecom integration.

TexecomConfigFlow is put together from compartments in flows/: connect.py and
crestron.py (each protocol's setup screens), reauth_reconfigure.py, options.py
(the options flow) and validation.py (the checks and form fields they share).
This module has the first menu and the arm modes step both protocols end on.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import BooleanSelector

from .const import CONF_CREATE_DASHBOARD, CONF_PROTOCOL, CONF_TIME_SYNC, DOMAIN, HELP_PART_ARMS, PROTOCOL_CRESTRON
from .entity import nice_name
from .flows.connect import ConnectSteps
from .flows.crestron import CrestronSteps
from .flows.options import TexecomOptionsFlow
from .flows.reauth_reconfigure import ReauthReconfigureSteps
from .flows.validation import arm_mode_options, arm_modes_error, arm_modes_schema, with_dashboard_choice


class TexecomConfigFlow(ConnectSteps, CrestronSteps, ReauthReconfigureSteps, ConfigFlow, domain=DOMAIN):
    VERSION = 1
    MINOR_VERSION = 3  # 2: 0.2.11's defaults; 3: the dashboard's buttons follow Arm buttons (see async_migrate_entry)

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return TexecomOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["connect", "crestron"])

    # ─── Arm modes (both protocols) ─────────────────────────────────────────

    async def async_step_arm_modes(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        crestron = self._data[CONF_PROTOCOL] == PROTOCOL_CRESTRON
        if user_input is not None:
            if error := arm_modes_error(user_input):
                errors["base"] = error
            else:
                # The dashboard is built once the entities exist (in setup).
                data = {**self._data, CONF_CREATE_DASHBOARD: bool(user_input.get(CONF_CREATE_DASHBOARD))}
                options = arm_mode_options(user_input)
                if not crestron:
                    # Setting the clock writes to the panel, so only when the
                    # user ticks it (off otherwise; Repairs offers it later if
                    # the clock is wrong, and Options → Panel clock changes it).
                    options[CONF_TIME_SYNC] = bool(user_input.get(CONF_TIME_SYNC))
                return self.async_create_entry(title=self._title(), data=data, options=options)
        placeholders = {"zones": "", "areas": "", "help": HELP_PART_ARMS}
        if not crestron:
            placeholders["zones"] = ", ".join(nice_name(z["name"]) for z in self._data["zones"])
            placeholders["areas"] = ", ".join(nice_name(a["name"]) for a in self._data["areas"])
        schema = arm_modes_schema(user_input or {}, crestron)
        if not crestron:
            schema = schema.extend({vol.Required(CONF_TIME_SYNC, default=False): BooleanSelector()})
        return self.async_show_form(
            step_id="arm_modes_crestron" if crestron else "arm_modes",
            data_schema=with_dashboard_choice(schema),
            errors=errors,
            description_placeholders=placeholders,
            last_step=True,
        )

    async_step_arm_modes_crestron = async_step_arm_modes

    def _title(self) -> str:
        info = self._data.get("info")
        if info and info.get("zones"):
            return f"Texecom {info['model']} {info['zones']}"
        return "Texecom Premier Elite"
