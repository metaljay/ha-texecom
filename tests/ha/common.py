"""Helpers for the Home Assistant tests."""

from __future__ import annotations

import asyncio
import socket
from typing import TYPE_CHECKING

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.texecom.connect import discovery as connect_discovery
from custom_components.texecom.const import DOMAIN
from custom_components.texecom.factory import layout_to_data

if TYPE_CHECKING:
    from fake_connect_panel import FakeConnectPanel

UDL = "1234"
ALARM = "alarm_control_panel.texecom_house"


async def wait_for(predicate, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not predicate():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.02)


async def layout_of(fake: FakeConnectPanel) -> dict:
    info, zones, areas = await connect_discovery.probe("127.0.0.1", fake.port, UDL, patience=0)
    return layout_to_data(info, zones, areas)


async def setup_connect(hass, fake: FakeConnectPanel, options: dict | None = None, data: dict | None = None):
    """Adds and sets up a Connect entry for the fake panel; waits until connected."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Texecom Premier Elite 24",
        unique_id=f"127.0.0.1:{fake.port}",
        data={
            "protocol": "connect",
            "host": "127.0.0.1",
            "port": fake.port,
            "udl": UDL,
            **(await layout_of(fake)),
            **(data or {}),
        },
        options={"night_part_arm": 1, "home_part_arm": 0, **(options or {})},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await wait_for(lambda: entry.runtime_data.connected)
    await hass.async_block_till_done()
    return entry


def state(hass) -> str:
    return hass.states.get(ALARM).state


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def start(hass, choice: str, sub_choice: str | None = None):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": choice})
    if sub_choice:
        assert result["type"] is FlowResultType.MENU
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": sub_choice})
    return result
