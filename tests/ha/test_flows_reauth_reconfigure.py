"""Reauth and reconfigure (flows/reauth_reconfigure.py)."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.texecom.const import DOMAIN

from .common import UDL, free_port, setup_connect, wait_for


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
