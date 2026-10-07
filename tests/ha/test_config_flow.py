"""The setup screens: Connect, Crestron (network and serial), reauth, reconfigure."""

from __future__ import annotations

import asyncio
import socket
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.texecom.const import DOMAIN
from custom_components.texecom.flows import validation

from .common import UDL, setup_connect, wait_for


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


@pytest.fixture
def no_setup():
    """Flow tests stop at the entry: don't connect."""
    with patch("custom_components.texecom.async_setup_entry", return_value=True) as mock:
        yield mock


# ─── Texecom Connect ────────────────────────────────────────────────────────


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


async def test_night_and_home_cannot_share_a_part_arm(hass, fake, no_setup):
    result = await start(hass, "connect")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "127.0.0.1", "port": fake.port, "udl": UDL}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"night_part_arm": "1", "home_part_arm": "1", "create_dashboard": False}
    )
    assert result["errors"] == {"base": "same_part_arm"}


# ─── Crestron ───────────────────────────────────────────────────────────────


@pytest.fixture
async def crestron_port(socket_enabled):
    """Answers ASTATUS like a panel's Crestron port ("N: area 1 not armed")."""
    state = {"answer": True}

    async def client(reader, writer):
        try:
            while data := await reader.read(256):
                if b"ASTATUS" in data and state["answer"]:
                    writer.write(b'"N\r\n')
        finally:
            writer.close()

    server = await asyncio.start_server(client, "127.0.0.1", 0)
    yield server.sockets[0].getsockname()[1], state
    server.close()


async def test_crestron_network(hass, crestron_port, no_setup):
    port, _state = crestron_port
    result = await start(hass, "crestron", "crestron_network")
    assert result["step_id"] == "crestron_network"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "127.0.0.1", "port": port, "zone_count": 5, "area_count": 1}
    )
    assert result["step_id"] == "arm_modes_crestron"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"night_part_arm": "1", "home_part_arm": "0", "keypad_arm_mode": "away", "create_dashboard": False},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["udl"] is None and result["data"]["zone_count"] == 5
    assert result["options"]["keypad_arm_mode"] == "away"


async def test_crestron_no_reply(hass, crestron_port, no_setup, monkeypatch):
    port, state = crestron_port
    state["answer"] = False
    monkeypatch.setattr(validation, "CRESTRON_REPLY_TIMEOUT", 0.2)
    result = await start(hass, "crestron", "crestron_network")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "127.0.0.1", "port": port, "zone_count": 5, "area_count": 1}
    )
    assert result["errors"] == {"base": "no_reply"}


@pytest.fixture
def fake_serial():
    """serial_asyncio_fast stand-in: opens fine unless told otherwise."""
    module = types.ModuleType("serial_asyncio_fast")
    writer = MagicMock()
    module.open_serial_connection = AsyncMock(return_value=(MagicMock(), writer))
    with patch.dict(sys.modules, {"serial_asyncio_fast": module}):
        yield module


async def test_crestron_serial(hass, fake_serial, no_setup):
    result = await start(hass, "crestron", "crestron_serial")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"serial_device": "/dev/ttyUSB0", "baud_rate": "19200", "udl": UDL, "zone_count": 8, "area_count": 1},
    )
    assert result["step_id"] == "arm_modes_crestron"
    fake_serial.open_serial_connection.assert_awaited_once_with(url="/dev/ttyUSB0", baudrate=19200)


async def test_crestron_serial_cannot_open(hass, fake_serial, no_setup):
    fake_serial.open_serial_connection.side_effect = OSError("no such device")
    result = await start(hass, "crestron", "crestron_serial")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"serial_device": "/dev/ttyUSB9", "baud_rate": "19200", "zone_count": 8, "area_count": 1},
    )
    assert result["errors"] == {"base": "cannot_open_serial"}


# ─── Reauth and reconfigure ─────────────────────────────────────────────────


async def test_reauth_after_the_udl_changes(hass, fake):
    fake.udl = "5678"  # the installer changed it
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"127.0.0.1:{fake.port}",
        data={
            "protocol": "connect",
            "host": "127.0.0.1",
            "port": fake.port,
            "udl": UDL,
            "info": {"model": "Premier Elite", "zones": 24, "firmware": "V6"},
            "zones": [],
            "areas": [{"number": 1, "name": "HOUSE"}],
        },
        options={"night_part_arm": 1, "home_part_arm": 0},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await wait_for(lambda: any(f["context"]["source"] == "reauth" for f in hass.config_entries.flow.async_progress()))
    flow = next(f for f in hass.config_entries.flow.async_progress() if f["context"]["source"] == "reauth")
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], {"udl": "5678"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reauth_successful"
    assert entry.data["udl"] == "5678"
    await hass.async_block_till_done()
    await wait_for(lambda: entry.runtime_data.connected)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_reconfigure_to_a_new_address(hass, fake):
    from fake_connect_panel import DEMO_ZONES, FakeConnectPanel

    entry = await setup_connect(hass, fake)
    moved = FakeConnectPanel(zones=DEMO_ZONES)
    await moved.start()
    try:
        result = await entry.start_reconfigure_flow(hass)
        assert result["step_id"] == "reconfigure_connect"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"host": "127.0.0.1", "port": moved.port, "udl": UDL}
        )
        assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
        assert entry.data["port"] == moved.port and entry.unique_id == f"127.0.0.1:{moved.port}"
        await hass.async_block_till_done()
        await wait_for(lambda: entry.runtime_data.connected)
        assert moved.writers  # connected to the new one
        assert await hass.config_entries.async_unload(entry.entry_id)
    finally:
        await moved.close()


async def test_reconfigure_failure_keeps_the_old_connection(hass, fake):
    entry = await setup_connect(hass, fake)
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "127.0.0.1", "port": free_port(), "udl": UDL}
    )
    assert result["errors"] == {"base": "cannot_connect"}
    assert entry.state is config_entries.ConfigEntryState.LOADED
    await wait_for(lambda: entry.runtime_data.connected)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_reconfigure_refuses_another_panels_address(hass, fake):
    entry = await setup_connect(hass, fake)
    MockConfigEntry(domain=DOMAIN, unique_id="127.0.0.1:1").add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "127.0.0.1", "port": 1, "udl": UDL}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"
    assert await hass.config_entries.async_unload(entry.entry_id)
