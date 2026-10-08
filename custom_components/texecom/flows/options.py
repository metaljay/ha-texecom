"""The integration's options, as a short menu:

- the Night and Home buttons (and, over Crestron, how a keypad arm shows)
- an optional Home Assistant alarm code
- names for keypad users (which can be filled in from the panel, over Connect)
- notifications for mains failures and tampers (Connect)
- keeping the panel clock right (Connect), or how often to check the panel
  (Crestron)
- reading zones and areas again (Connect), and rebuilding the Alarm dashboard

Saving an option the panel driver uses reconnects to the panel (see
__init__.py); the others apply straight away."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlowResult, OptionsFlow
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from ..const import (
    CONF_ALARM_CODE,
    CONF_CODE_ARM_REQUIRED,
    CONF_NOTIFY_MAINS,
    CONF_NOTIFY_TAMPER,
    CONF_PROTOCOL,
    CONF_STATUS_POLL,
    CONF_TIME_SYNC,
    CONF_USER_NAMES,
    DEFAULT_STATUS_POLL,
    HELP_CODES,
    HELP_DASHBOARD,
    HELP_OPTIONS,
    HELP_PART_ARMS,
    PROTOCOL_CONNECT,
)
from ..dashboard import async_create_dashboard
from ..factory import layout_to_data
from ..panel import PanelError
from ..users import format_user_names, parse_user_names
from .validation import UDL_SELECTOR, arm_mode_options, arm_modes_error, arm_modes_schema

_LOGGER = logging.getLogger(__name__)

READ_NAMES = "read_names"  # on the names form, not an option: fills in the panel's names
ENGINEER_USER, ENGINEER_NAME = "0", "Engineer"


class TexecomOptionsFlow(OptionsFlow):
    _read_task: asyncio.Task | None = None  # reading zones and areas again
    _read_result: tuple[int, int] | None = None  # (zones, areas) read, or None if it failed

    @property
    def _is_connect(self) -> bool:
        return self.config_entry.data[CONF_PROTOCOL] == PROTOCOL_CONNECT

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        menu = ["arm_modes", "alarm_code", "user_names"]
        menu += ["notices", "clock", "rediscover"] if self._is_connect else ["status_poll"]
        menu.append("dashboard")
        return self.async_show_menu(step_id="init", menu_options=menu, description_placeholders={"help": HELP_OPTIONS})

    # ─── Settings ───────────────────────────────────────────────────────────

    async def async_step_arm_modes(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        crestron = not self._is_connect
        errors: dict[str, str] = {}
        if user_input is not None:
            if error := arm_modes_error(user_input):
                errors["base"] = error
            else:
                return self._save(arm_mode_options(user_input))
        current = {**self.config_entry.options, **(user_input or {})}
        return self.async_show_form(
            step_id="arm_modes_crestron" if crestron else "arm_modes",
            data_schema=arm_modes_schema(current, crestron),
            errors=errors,
            description_placeholders={"help": HELP_PART_ARMS},
        )

    async_step_arm_modes_crestron = async_step_arm_modes

    async def async_step_alarm_code(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            code = (user_input.get(CONF_ALARM_CODE) or "").strip()
            if code and not code.isdigit():
                errors[CONF_ALARM_CODE] = "invalid_code_format"
            else:
                return self._save(
                    {
                        CONF_ALARM_CODE: code or None,
                        CONF_CODE_ARM_REQUIRED: bool(user_input.get(CONF_CODE_ARM_REQUIRED)),
                    }
                )
        current = {**self.config_entry.options, **(user_input or {})}
        schema = vol.Schema(
            {
                vol.Optional(CONF_ALARM_CODE, description={"suggested_value": current.get(CONF_ALARM_CODE)}): (
                    UDL_SELECTOR
                ),
                vol.Required(CONF_CODE_ARM_REQUIRED, default=current.get(CONF_CODE_ARM_REQUIRED, False)): (
                    BooleanSelector()
                ),
            }
        )
        return self.async_show_form(
            step_id="alarm_code", data_schema=schema, errors=errors, description_placeholders={"help": HELP_CODES}
        )

    async def async_step_user_names(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        text = format_user_names(self.config_entry.options.get(CONF_USER_NAMES, {}))
        if user_input is not None:
            text = user_input.get(CONF_USER_NAMES, "")
            if (names := parse_user_names(text)) is None:
                errors[CONF_USER_NAMES] = "invalid_user_names"
            elif user_input.get(READ_NAMES):
                # Shown again with the panel's names added, to check before saving.
                text, error = await self._with_panel_names(names)
                if error:
                    errors["base"] = error
            else:
                return self._save({CONF_USER_NAMES: names})
        fields: dict[Any, Any] = {
            vol.Optional(CONF_USER_NAMES, description={"suggested_value": text}): TextSelector(
                TextSelectorConfig(multiline=True)
            )
        }
        if self._is_connect:
            fields[vol.Optional(READ_NAMES, default=False)] = BooleanSelector()
        return self.async_show_form(step_id="user_names", data_schema=vol.Schema(fields), errors=errors)

    async def _with_panel_names(self, names: dict[str, str]) -> tuple[str, str | None]:
        """The names typed so far plus the panel's for the other users, and an
        error key if the panel couldn't be read or has no named users."""
        try:
            from_panel = await self.config_entry.runtime_data.async_read_user_names()
        except (PanelError, AttributeError) as err:
            _LOGGER.debug("Reading the users from the panel failed: %s", err)
            return format_user_names(names), "read_names_failed"
        if not from_panel:
            return format_user_names(names), "no_user_names"
        # The engineer code shows as user 0 in keypad events, but has no user
        # record to read (the panel refuses user 0), so it's named here.
        found = {ENGINEER_USER: ENGINEER_NAME, **{str(n): name for n, name in from_panel.items()}}
        return format_user_names({**found, **names}), None

    async def async_step_notices(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self._save(
                {
                    CONF_NOTIFY_MAINS: bool(user_input.get(CONF_NOTIFY_MAINS)),
                    CONF_NOTIFY_TAMPER: bool(user_input.get(CONF_NOTIFY_TAMPER)),
                }
            )
        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(CONF_NOTIFY_MAINS, default=options.get(CONF_NOTIFY_MAINS, True)): BooleanSelector(),
                vol.Required(CONF_NOTIFY_TAMPER, default=options.get(CONF_NOTIFY_TAMPER, True)): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="notices", data_schema=schema)

    async def async_step_clock(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self._save({CONF_TIME_SYNC: bool(user_input.get(CONF_TIME_SYNC))})
        default = self.config_entry.options.get(CONF_TIME_SYNC, False)
        schema = vol.Schema({vol.Required(CONF_TIME_SYNC, default=default): BooleanSelector()})
        return self.async_show_form(step_id="clock", data_schema=schema)

    async def async_step_status_poll(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self._save({CONF_STATUS_POLL: int(user_input[CONF_STATUS_POLL])})
        default = self.config_entry.options.get(CONF_STATUS_POLL, DEFAULT_STATUS_POLL)
        schema = vol.Schema(
            {
                vol.Required(CONF_STATUS_POLL, default=default): NumberSelector(
                    NumberSelectorConfig(min=0, max=600, step=10, mode=NumberSelectorMode.BOX, unit_of_measurement="s")
                )
            }
        )
        return self.async_show_form(step_id="status_poll", data_schema=schema)

    def _save(self, changes: dict[str, Any]) -> ConfigFlowResult:
        return self.async_create_entry(data={**self.config_entry.options, **changes})

    # ─── Actions ────────────────────────────────────────────────────────────

    async def async_step_rediscover(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Reads zones and areas again over the open session (the panel allows
        only one), with a progress screen meanwhile."""
        if self._read_task is None:
            self._read_task = self.hass.async_create_task(self._read_layout())
        if not self._read_task.done():
            return self.async_show_progress(
                step_id="rediscover", progress_action="reading", progress_task=self._read_task
            )
        self._read_result, self._read_task = self._read_task.result(), None
        return self.async_show_progress_done(next_step_id="rediscover_result")

    async def async_step_rediscover_result(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._read_result is None:
            return self.async_abort(reason="rediscover_failed")
        zones, areas = self._read_result
        return self.async_abort(
            reason="rediscovered", description_placeholders={"zones": str(zones), "areas": str(areas)}
        )

    async def _read_layout(self) -> tuple[int, int] | None:
        entry = self.config_entry
        try:
            info, zones, areas = await entry.runtime_data.async_rediscover()
        except (PanelError, AttributeError) as err:
            _LOGGER.debug("Re-reading the panel failed: %s", err)
            return None
        self.hass.config_entries.async_update_entry(entry, data={**entry.data, **layout_to_data(info, zones, areas)})
        # A change to the stored data doesn't reload by itself; new zones need one.
        self.hass.config_entries.async_schedule_reload(entry.entry_id)
        return len(zones), len(areas)

    async def async_step_dashboard(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is None:  # asks first: it replaces any changes made to the dashboard
            return self.async_show_form(step_id="dashboard", data_schema=vol.Schema({}))
        try:
            await async_create_dashboard(self.hass, self.config_entry)
        except HomeAssistantError:
            return self.async_abort(reason="dashboard_failed", description_placeholders={"help": HELP_DASHBOARD})
        return self.async_abort(reason="dashboard_created")
