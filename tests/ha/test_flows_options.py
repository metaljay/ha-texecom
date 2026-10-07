"""The integration's options (flows/options.py), including a dashboard
that can't be created."""

from __future__ import annotations

import asyncio

from homeassistant.helpers import device_registry as dr

from .common import setup_connect, wait_for

OPTIONS = {
    "night_part_arm": "1",
    "home_part_arm": "0",
    "code_arm_required": False,
    "create_dashboard": False,
    "time_sync": False,
    "rediscover": False,
}


async def options(hass, entry, **changes):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    return await hass.config_entries.options.async_configure(result["flow_id"], {**OPTIONS, **changes})


async def test_options_validation_and_save(hass, fake):
    entry = await setup_connect(hass, fake)
    result = await options(hass, entry, alarm_code="12a")
    assert result["errors"] == {"alarm_code": "invalid_code_format"}
    result = await options(hass, entry, home_part_arm="1")
    assert result["errors"] == {"base": "same_part_arm"}
    result = await options(hass, entry, home_part_arm="2", alarm_code="4321")
    assert result["type"] == "create_entry"
    assert entry.options["home_part_arm"] == 2 and entry.options["alarm_code"] == "4321"
    await hass.async_block_till_done()
    await wait_for(lambda: entry.runtime_data.connected)  # reloaded with the new options
    assert hass.states.get("alarm_control_panel.texecom_house").attributes["supported_features"] == 1 | 2 | 4
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_read_zones_again_uses_the_open_connection(hass, fake):
    entry = await setup_connect(hass, fake)
    fake.zone_list = [(1, "Porch Door", 1), *fake.zone_list[1:]]  # renamed in Wintex
    fake.writers_before = set(fake.writers)
    result = await options(hass, entry, rediscover=True)
    assert result["type"] == "create_entry"
    assert fake.writers_before  # it didn't need a second login to do it
    await hass.async_block_till_done()
    await wait_for(lambda: entry.runtime_data.connected)
    names = {d.name for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)}
    assert "Porch Door" in names
    assert entry.data["zones"][0]["name"] == "Porch Door"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_read_zones_again_with_other_changes_reloads_once(hass, fake):
    entry = await setup_connect(hass, fake)
    connections = fake.connections
    result = await options(hass, entry, rediscover=True, time_sync=True)
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    await wait_for(lambda: entry.runtime_data.connected)
    for _ in range(5):  # let any second reload happen
        await asyncio.sleep(0.1)
        await hass.async_block_till_done()
    await wait_for(lambda: entry.runtime_data.connected)
    # One reload, one new connection: a SmartCom refuses a second for a minute.
    assert fake.connections == connections + 1
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_dashboard_without_lovelace_fails_gently(hass, fake):
    entry = await setup_connect(hass, fake)
    result = await options(hass, entry, create_dashboard=True)
    assert result["errors"] == {"base": "dashboard_failed"}
    assert await hass.config_entries.async_unload(entry.entry_id)
