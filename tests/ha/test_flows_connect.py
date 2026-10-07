"""The Texecom Connect setup screen (flows/connect.py)."""

from __future__ import annotations

from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.texecom.const import DOMAIN

from .common import UDL, free_port, start


async def test_connect_happy_path(hass, fake, no_setup):
    result = await start(hass, "connect")
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "connect"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "127.0.0.1", "port": fake.port, "udl": UDL}
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "arm_modes"
    assert "Front Door" in result["description_placeholders"]["zones"]
    assert result["description_placeholders"]["areas"] == "House"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"night_part_arm": "1", "home_part_arm": "0", "create_dashboard": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Texecom Premier Elite 24"
    assert result["data"]["protocol"] == "connect" and result["data"]["create_dashboard"] is True
    assert [z["name"] for z in result["data"]["zones"]][:2] == ["Front Door", "Hallway"]
    assert result["options"] == {"night_part_arm": 1, "home_part_arm": 0}
    assert result["result"].unique_id == f"127.0.0.1:{fake.port}"


async def test_connect_wrong_udl(hass, fake, no_setup):
    result = await start(hass, "connect")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "127.0.0.1", "port": fake.port, "udl": "9999"}
    )
    assert result["errors"] == {"base": "invalid_auth"}


async def test_connect_unreachable(hass, socket_enabled, no_setup):
    result = await start(hass, "connect")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "127.0.0.1", "port": free_port(), "udl": UDL}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_connect_udl_must_be_digits(hass, fake, no_setup):
    result = await start(hass, "connect")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "127.0.0.1", "port": fake.port, "udl": "12a"}
    )
    assert result["errors"] == {"udl": "invalid_udl"}


async def test_connect_already_configured(hass, fake, no_setup):
    MockConfigEntry(domain=DOMAIN, unique_id=f"127.0.0.1:{fake.port}").add_to_hass(hass)
    result = await start(hass, "connect")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "127.0.0.1", "port": fake.port, "udl": UDL}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"
