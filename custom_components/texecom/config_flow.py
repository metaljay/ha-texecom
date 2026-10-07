"""Config flow for the Texecom integration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import layout_to_data
from .connect.client import ConnectError, LoginRejected
from .connect.panel import probe
from .const import (
    CONF_ALARM_CODE,
    CONF_AREA_COUNT,
    CONF_BAUD_RATE,
    CONF_CODE_ARM_REQUIRED,
    CONF_CONNECTION,
    CONF_CREATE_DASHBOARD,
    CONF_HOME_PART_ARM,
    CONF_KEYPAD_ARM_MODE,
    CONF_NIGHT_PART_ARM,
    CONF_PROTOCOL,
    CONF_REDISCOVER,
    CONF_SERIAL_DEVICE,
    CONF_STATUS_POLL,
    CONF_TIME_SYNC,
    CONF_UDL,
    CONF_ZONE_COUNT,
    CONNECTION_NETWORK,
    CONNECTION_SERIAL,
    DEFAULT_BAUD_RATE,
    DEFAULT_CONNECT_PORT,
    DEFAULT_CRESTRON_PORT,
    DEFAULT_STATUS_POLL,
    DEFAULT_UDL,
    DOMAIN,
    PROTOCOL_CONNECT,
    PROTOCOL_CRESTRON,
)
from .dashboard import async_create_dashboard
from .entity import nice_name
from .panel import PanelError

_LOGGER = logging.getLogger(__name__)

PART_ARM_SELECTOR = SelectSelector(
    SelectSelectorConfig(options=["0", "1", "2", "3"], translation_key="part_arm", mode=SelectSelectorMode.DROPDOWN)
)
KEYPAD_MODE_SELECTOR = SelectSelector(
    SelectSelectorConfig(
        options=["away", "home", "night"], translation_key="keypad_arm_mode", mode=SelectSelectorMode.DROPDOWN
    )
)
UDL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
PORT_SELECTOR = NumberSelector(NumberSelectorConfig(min=1, max=65535, mode=NumberSelectorMode.BOX))


CRESTRON_REPLY_TIMEOUT = 5.0  # seconds to wait for the panel to answer ASTATUS


def _udl_valid(udl: str) -> bool:
    return udl.isdigit() and 4 <= len(udl) <= 8


async def _validate_connect(host: str, port: int, udl: str) -> tuple[dict[str, Any] | None, str | None]:
    """Logs in and reads the panel's zones and areas: (data, error key)."""
    try:
        info, zones, areas = await asyncio.wait_for(probe(host, port, udl), 180)
    except LoginRejected:
        return None, "invalid_auth"
    except (ConnectError, TimeoutError, OSError) as err:
        _LOGGER.debug("Connect probe failed: %s", err)
        return None, "cannot_connect"
    if not zones:
        return None, "no_zones"
    return {**layout_to_data(info, zones, areas)}, None


async def _validate_crestron_network(host: str, port: int) -> str | None:
    """Opens the connection and checks the panel answers ASTATUS."""
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), 10)
    except (OSError, TimeoutError):
        return "cannot_connect"
    try:
        writer.write(b"ASTATUS\r\n")
        data = b""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + CRESTRON_REPLY_TIMEOUT
        while b'"' not in data and loop.time() < deadline:
            with contextlib.suppress(TimeoutError):
                data += await asyncio.wait_for(reader.read(256), max(0.1, deadline - loop.time()))
        # Any quoted reply means a Crestron port; ASTATUS is answered "Y/"N...
        return None if b'"' in data else "no_reply"
    except OSError:
        return "cannot_connect"
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


async def _validate_serial(device: str, baud_rate: int) -> str | None:
    """Checks the serial device opens (its owner or path is the usual problem)."""
    try:
        from serial_asyncio_fast import open_serial_connection

        _reader, writer = await asyncio.wait_for(open_serial_connection(url=device, baudrate=baud_rate), 10)
    except (OSError, TimeoutError, ImportError, ValueError) as err:
        _LOGGER.debug("Opening %s failed: %s", device, err)
        return "cannot_open_serial"
    writer.close()
    return None


def _arm_modes_schema(defaults: Mapping[str, Any], crestron: bool) -> vol.Schema:
    schema: dict[Any, Any] = {
        vol.Required(CONF_NIGHT_PART_ARM, default=str(defaults.get(CONF_NIGHT_PART_ARM, 1))): PART_ARM_SELECTOR,
        vol.Required(CONF_HOME_PART_ARM, default=str(defaults.get(CONF_HOME_PART_ARM, 0))): PART_ARM_SELECTOR,
    }
    if crestron:
        schema[vol.Required(CONF_KEYPAD_ARM_MODE, default=defaults.get(CONF_KEYPAD_ARM_MODE, "away"))] = (
            KEYPAD_MODE_SELECTOR
        )
    return vol.Schema(schema)


def _with_dashboard_choice(schema: vol.Schema) -> vol.Schema:
    return schema.extend({vol.Required(CONF_CREATE_DASHBOARD, default=True): BooleanSelector()})


def _arm_mode_options(user_input: Mapping[str, Any]) -> dict[str, Any]:
    options = {
        CONF_NIGHT_PART_ARM: int(user_input[CONF_NIGHT_PART_ARM]),
        CONF_HOME_PART_ARM: int(user_input[CONF_HOME_PART_ARM]),
    }
    if CONF_KEYPAD_ARM_MODE in user_input:
        options[CONF_KEYPAD_ARM_MODE] = user_input[CONF_KEYPAD_ARM_MODE]
    return options


def _arm_modes_error(options: Mapping[str, Any]) -> str | None:
    home, night = int(options[CONF_HOME_PART_ARM]), int(options[CONF_NIGHT_PART_ARM])
    return "same_part_arm" if home and home == night else None


class TexecomConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return TexecomOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["connect", "crestron"])

    # ─── Texecom Connect ────────────────────────────────────────────────────

    async def async_step_connect(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host, port, udl = user_input[CONF_HOST].strip(), int(user_input[CONF_PORT]), user_input[CONF_UDL].strip()
            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()
            if not _udl_valid(udl):
                errors[CONF_UDL] = "invalid_udl"
            else:
                layout, error = await _validate_connect(host, port, udl)
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

    # ─── Crestron ───────────────────────────────────────────────────────────

    async def async_step_crestron(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="crestron", menu_options=["crestron_network", "crestron_serial"])

    def _crestron_common(self, defaults: Mapping[str, Any]) -> dict[Any, Any]:
        return {
            vol.Optional(CONF_UDL, description={"suggested_value": defaults.get(CONF_UDL, DEFAULT_UDL)}): UDL_SELECTOR,
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
            if udl and not _udl_valid(udl):
                errors[CONF_UDL] = "invalid_udl"
            elif error := await _validate_crestron_network(host, port):
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
        return self.async_show_form(step_id="crestron_network", data_schema=schema, errors=errors)

    async def async_step_crestron_serial(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            device = user_input[CONF_SERIAL_DEVICE].strip()
            await self.async_set_unique_id(f"serial:{device}")
            self._abort_if_unique_id_configured()
            udl = (user_input.get(CONF_UDL) or "").strip()
            if udl and not _udl_valid(udl):
                errors[CONF_UDL] = "invalid_udl"
            elif error := await _validate_serial(device, int(user_input[CONF_BAUD_RATE])):
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
        return self.async_show_form(step_id="crestron_serial", data_schema=schema, errors=errors)

    # ─── Arm modes (both protocols) ─────────────────────────────────────────

    async def async_step_arm_modes(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        crestron = self._data[CONF_PROTOCOL] == PROTOCOL_CRESTRON
        if user_input is not None:
            if error := _arm_modes_error(user_input):
                errors["base"] = error
            else:
                # The dashboard is built once the entities exist (in setup).
                data = {**self._data, CONF_CREATE_DASHBOARD: bool(user_input.get(CONF_CREATE_DASHBOARD))}
                return self.async_create_entry(title=self._title(), data=data, options=_arm_mode_options(user_input))
        placeholders = {"zones": "", "areas": ""}
        if not crestron:
            placeholders = {
                "zones": ", ".join(nice_name(z["name"]) for z in self._data["zones"]),
                "areas": ", ".join(nice_name(a["name"]) for a in self._data["areas"]),
            }
        return self.async_show_form(
            step_id="arm_modes_crestron" if crestron else "arm_modes",
            data_schema=_with_dashboard_choice(_arm_modes_schema(user_input or {}, crestron)),
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

    # ─── Reauth / reconfigure ───────────────────────────────────────────────

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            udl = user_input[CONF_UDL].strip()
            if not _udl_valid(udl):
                errors[CONF_UDL] = "invalid_udl"
            else:
                _layout, error = await _validate_connect(entry.data[CONF_HOST], entry.data[CONF_PORT], udl)
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
            if (udl or is_connect) and not _udl_valid(udl):
                errors[CONF_UDL] = "invalid_udl"
            else:
                # The panel allows one session: pause ours while checking.
                await self._pause(entry)
                if is_connect:
                    layout, error = await _validate_connect(updates[CONF_HOST], updates[CONF_PORT], udl)
                    if error:
                        errors["base"] = error
                    else:
                        updates.update(layout)
                elif is_serial:
                    if error := await _validate_serial(
                        updates[CONF_SERIAL_DEVICE], data.get(CONF_BAUD_RATE, DEFAULT_BAUD_RATE)
                    ):
                        errors["base"] = error
                elif error := await _validate_crestron_network(updates[CONF_HOST], updates[CONF_PORT]):
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


class TexecomOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self.config_entry
        is_connect = entry.data[CONF_PROTOCOL] == PROTOCOL_CONNECT
        errors: dict[str, str] = {}
        if user_input is not None:
            code = (user_input.get(CONF_ALARM_CODE) or "").strip()
            if error := _arm_modes_error(user_input):
                errors["base"] = error
            elif code and not code.isdigit():
                errors[CONF_ALARM_CODE] = "invalid_code_format"
            else:
                options = _arm_mode_options(user_input)
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
                            self.hass.config_entries.async_schedule_reload(entry.entry_id)
                else:
                    options[CONF_STATUS_POLL] = int(user_input[CONF_STATUS_POLL])
                if not errors and user_input.get(CONF_CREATE_DASHBOARD):
                    try:
                        await async_create_dashboard(self.hass, entry)
                    except HomeAssistantError:
                        errors["base"] = "dashboard_failed"
                if not errors:
                    return self.async_create_entry(data=options)

        current = {**entry.options, **(user_input or {})}
        schema = _arm_modes_schema(current, crestron=not is_connect).schema
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
