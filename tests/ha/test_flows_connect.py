"""The Texecom Connect setup screen (flows/connect.py)."""

from __future__ import annotations

from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.texecom.const import DOMAIN

from .common import UDL, after_progress, free_port, start


async def test_connect_happy_path(hass, fake, no_setup):
    result = await start(hass, "connect")
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "connect"
    assert result["description_placeholders"]["help"].startswith("https://")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "127.0.0.1", "port": fake.port, "udl": UDL}
    )
    # "Connecting to your panel…" while it logs in and reads the zones
    assert result["type"] is FlowResultType.SHOW_PROGRESS and result["progress_action"] == "connecting"
    result = await after_progress(hass, result)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "arm_modes"
    assert "Front Door" in result["description_placeholders"]["zones"]
    assert result["description_placeholders"]["areas"] == "House"
    assert result["description_placeholders"]["help"].endswith("#part-arms-explained")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"night_part_arm": "1", "home_part_arm": "0", "create_dashboard": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Texecom Premier Elite 24"
    assert result["data"]["protocol"] == "connect" and result["data"]["create_dashboard"] is True
    assert [z["name"] for z in result["data"]["zones"]][:2] == ["Front Door", "Hallway"]
    # Keep the panel clock right writes to the panel, so it's off unless ticked.
    assert result["options"] == {"night_part_arm": 1, "home_part_arm": 0, "time_sync": False}
    assert result["result"].unique_id == f"127.0.0.1:{fake.port}"


async def test_connect_clock_is_a_choice(hass, fake, no_setup):
    """The arm modes step offers Keep the panel clock right, unticked."""
    result = await start(hass, "connect")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "127.0.0.1", "port": fake.port, "udl": UDL}
    )
    result = await after_progress(hass, result)
    assert result["data_schema"]({"night_part_arm": "1", "home_part_arm": "0"})["time_sync"] is False
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"night_part_arm": "1", "home_part_arm": "0", "time_sync": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY and result["options"]["time_sync"] is True


async def test_connect_wrong_udl(hass, fake, no_setup):
    result = await start(hass, "connect")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "127.0.0.1", "port": fake.port, "udl": "9999"}
    )
    result = await after_progress(hass, result)
    assert result["step_id"] == "connect" and result["errors"] == {"base": "invalid_auth"}
    # what was entered is still there to correct
    assert result["data_schema"]({})["port"] == fake.port


async def test_connect_unreachable(hass, socket_enabled, no_setup):
    result = await start(hass, "connect")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "127.0.0.1", "port": free_port(), "udl": UDL}
    )
    result = await after_progress(hass, result)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_connect_address_typo(hass, socket_enabled, no_setup):
    """An address that can't be one (a typo) is "couldn't connect, check the
    address", not "unknown error". (What Python raises for it; the tests
    can't look addresses up.)"""
    from unittest.mock import patch

    result = await start(hass, "connect")
    with patch(
        "asyncio.open_connection", side_effect=UnicodeEncodeError("idna", "192.168.1..5", 10, 11, "label empty")
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": "192.168.1..5", "port": 10001, "udl": UDL}
        )
        result = await after_progress(hass, result)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_connect_unexpected_error(hass, socket_enabled, no_setup, caplog):
    """Anything else unexpected says so on the screen, with the details in the
    log. (It fails at once, before the check has waited for anything: the
    progress screen must still come first, or Home Assistant submits the
    form again and again, frozen.)"""
    from unittest.mock import patch

    result = await start(hass, "connect")
    with patch("custom_components.texecom.flows.validation.probe", side_effect=RuntimeError("surprise")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": "127.0.0.1", "port": free_port(), "udl": UDL}
        )
        result = await after_progress(hass, result)
    assert result["errors"] == {"base": "unknown"}
    assert "surprise" in caplog.text


async def test_connect_then_corrected(hass, fake, no_setup):
    """A wrong code, then the right one: the second try goes through."""
    result = await start(hass, "connect")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "127.0.0.1", "port": fake.port, "udl": "9999"}
    )
    result = await after_progress(hass, result)
    assert result["errors"] == {"base": "invalid_auth"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "127.0.0.1", "port": fake.port, "udl": UDL}
    )
    result = await after_progress(hass, result)
    assert result["step_id"] == "arm_modes"


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
