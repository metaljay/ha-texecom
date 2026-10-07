"""Shared by the setup, reauth, reconfigure and options screens: checking
what was entered (the UDL code, a Connect login, a Crestron port, a serial
device, the arm modes) and the form fields more than one screen uses."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
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

from ..connect.client import ConnectError, LoginRejected
from ..connect.discovery import probe
from ..const import CONF_CREATE_DASHBOARD, CONF_HOME_PART_ARM, CONF_KEYPAD_ARM_MODE, CONF_NIGHT_PART_ARM
from ..factory import layout_to_data

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


def udl_valid(udl: str) -> bool:
    return udl.isdigit() and 4 <= len(udl) <= 8


async def validate_connect(host: str, port: int, udl: str) -> tuple[dict[str, Any] | None, str | None]:
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


async def validate_crestron_network(host: str, port: int) -> str | None:
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


async def validate_serial(device: str, baud_rate: int) -> str | None:
    """Checks the serial device opens (its owner or path is the usual problem)."""
    try:
        from serial_asyncio_fast import open_serial_connection

        _reader, writer = await asyncio.wait_for(open_serial_connection(url=device, baudrate=baud_rate), 10)
    except (OSError, TimeoutError, ImportError, ValueError) as err:
        _LOGGER.debug("Opening %s failed: %s", device, err)
        return "cannot_open_serial"
    writer.close()
    return None


def arm_modes_schema(defaults: Mapping[str, Any], crestron: bool) -> vol.Schema:
    schema: dict[Any, Any] = {
        vol.Required(CONF_NIGHT_PART_ARM, default=str(defaults.get(CONF_NIGHT_PART_ARM, 1))): PART_ARM_SELECTOR,
        vol.Required(CONF_HOME_PART_ARM, default=str(defaults.get(CONF_HOME_PART_ARM, 0))): PART_ARM_SELECTOR,
    }
    if crestron:
        schema[vol.Required(CONF_KEYPAD_ARM_MODE, default=defaults.get(CONF_KEYPAD_ARM_MODE, "away"))] = (
            KEYPAD_MODE_SELECTOR
        )
    return vol.Schema(schema)


def with_dashboard_choice(schema: vol.Schema) -> vol.Schema:
    return schema.extend({vol.Required(CONF_CREATE_DASHBOARD, default=True): BooleanSelector()})


def arm_mode_options(user_input: Mapping[str, Any]) -> dict[str, Any]:
    options = {
        CONF_NIGHT_PART_ARM: int(user_input[CONF_NIGHT_PART_ARM]),
        CONF_HOME_PART_ARM: int(user_input[CONF_HOME_PART_ARM]),
    }
    if CONF_KEYPAD_ARM_MODE in user_input:
        options[CONF_KEYPAD_ARM_MODE] = user_input[CONF_KEYPAD_ARM_MODE]
    return options


def arm_modes_error(options: Mapping[str, Any]) -> str | None:
    home, night = int(options[CONF_HOME_PART_ARM]), int(options[CONF_NIGHT_PART_ARM])
    return "same_part_arm" if home and home == night else None
