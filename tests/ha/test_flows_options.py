"""The options menu (flows/options.py), and what each entry in it does."""

from __future__ import annotations

import asyncio

from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.texecom.connect import protocol as P
from custom_components.texecom.const import DOMAIN

from .common import ALARM, after_progress, setup_connect, wait_for

CRESTRON = {
    "protocol": "crestron",
    "connection": "network",
    "host": "127.0.0.1",
    "port": 1,
    "udl": "1234",
    "zone_count": 3,
    "area_count": 1,
}


async def open_options(hass, entry, choice):
    """Opens Configure and picks an entry from the menu."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU and result["step_id"] == "init"
    return await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": choice})


async def options(hass, entry, choice, **values):
    """Picks a menu entry and submits its form."""
    result = await open_options(hass, entry, choice)
    return await hass.config_entries.options.async_configure(result["flow_id"], values)


async def settle(hass) -> None:
    """Lets any reload run, so connections can be counted."""
    for _ in range(5):
        await asyncio.sleep(0.1)
        await hass.async_block_till_done()


async def test_the_menu(hass, fake):
    entry = await setup_connect(hass, fake)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"] == [
        "arm_modes",
        "alarm_code",
        "user_names",
        "notices",
        "clock",
        "rediscover",
        "dashboard",
    ]
    assert result["description_placeholders"]["help"].startswith("https://")
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_the_menu_for_crestron(hass):
    entry = MockConfigEntry(domain=DOMAIN, data=CRESTRON, options={"night_part_arm": 1, "home_part_arm": 0})
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["menu_options"] == ["arm_modes", "alarm_code", "user_names", "status_poll", "dashboard"]
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": "arm_modes"})
    assert result["step_id"] == "arm_modes_crestron"  # with "when armed at the keypad, show as"
    result = await options(hass, entry, "status_poll", status_poll=120)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"night_part_arm": 1, "home_part_arm": 0, "status_poll": 120}


async def test_night_and_home_buttons_reconnect(hass, fake):
    entry = await setup_connect(hass, fake)
    result = await options(hass, entry, "arm_modes", night_part_arm="1", home_part_arm="1")
    assert result["errors"] == {"base": "same_part_arm"}
    connections = fake.connections
    result = await options(hass, entry, "arm_modes", night_part_arm="1", home_part_arm="2")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["home_part_arm"] == 2
    await settle(hass)
    await wait_for(lambda: entry.runtime_data.connected)  # reconnected with the new buttons
    assert fake.connections == connections + 1
    assert hass.states.get(ALARM).attributes["supported_features"] == 1 | 2 | 4
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_alarm_code_applies_without_reconnecting(hass, fake):
    entry = await setup_connect(hass, fake)
    result = await options(hass, entry, "alarm_code", alarm_code="12a", code_arm_required=False)
    assert result["errors"] == {"alarm_code": "invalid_code_format"}
    connections = fake.connections
    result = await options(hass, entry, "alarm_code", alarm_code="4321", code_arm_required=True)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["alarm_code"] == "4321" and entry.options["code_arm_required"] is True
    assert entry.options["night_part_arm"] == 1  # the other options are kept
    await settle(hass)
    assert hass.states.get(ALARM).attributes["code_format"] == "number"
    assert hass.states.get(ALARM).attributes["code_arm_required"] is True
    assert fake.connections == connections  # no reconnect
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_names_for_keypad_users(hass, fake):
    entry = await setup_connect(hass, fake)
    result = await options(hass, entry, "user_names", user_names="Sam is user three")
    assert result["errors"] == {"user_names": "invalid_user_names"}
    connections = fake.connections
    result = await options(hass, entry, "user_names", user_names="1 = Alex\nUser 3: Sam\n")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["user_names"] == {"1": "Alex", "3": "Sam"}
    await settle(hass)
    assert fake.connections == connections  # no reconnect
    fake.send_user(3)
    fake.set_area(3)  # armed at the keypad by user 3
    await wait_for(lambda: hass.states.get(ALARM).attributes.get("changed_by") == "Sam")
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_notifications_setting(hass, fake):
    entry = await setup_connect(hass, fake)
    result = await open_options(hass, entry, "notices")
    assert result["data_schema"]({}) == {"notify_mains": True, "notify_tamper": True}  # on unless turned off
    result = await options(hass, entry, "notices", notify_mains=False, notify_tamper=True)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["notify_mains"] is False and entry.options["notify_tamper"] is True
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_clock_setting_reconnects(hass, fake):
    entry = await setup_connect(hass, fake)
    connections = fake.connections
    result = await options(hass, entry, "clock", time_sync=True)
    assert result["type"] is FlowResultType.CREATE_ENTRY and entry.options["time_sync"] is True
    await settle(hass)
    await wait_for(lambda: entry.runtime_data.connected)
    assert fake.connections == connections + 1
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_read_zones_again_uses_the_open_connection(hass, fake):
    entry = await setup_connect(hass, fake)
    fake.zone_list = [(1, "Porch Door", 1), *fake.zone_list[1:]]  # renamed in Wintex
    connections = fake.connections
    result = await open_options(hass, entry, "rediscover")
    assert result["type"] is FlowResultType.SHOW_PROGRESS and result["progress_action"] == "reading"
    result = await after_progress(hass, result, hass.config_entries.options)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "rediscovered"
    assert result["description_placeholders"] == {"zones": "8", "areas": "1"}
    await settle(hass)
    await wait_for(lambda: entry.runtime_data.connected)
    names = {d.name for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)}
    assert "Porch Door" in names
    assert entry.data["zones"][0]["name"] == "Porch Door"
    # Read over the open session, then one reload: one new connection in all
    # (a SmartCom refuses a second for a minute).
    assert fake.connections == connections + 1
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_read_zones_again_when_the_panel_doesnt_answer(hass, fake):
    entry = await setup_connect(hass, fake)
    fake.nak_next[P.CMD_GET_PANEL_IDENTIFICATION] = 1  # busy
    result = await open_options(hass, entry, "rediscover")
    result = await after_progress(hass, result, hass.config_entries.options)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "rediscover_failed"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_dashboard_without_lovelace_fails_gently(hass, fake):
    entry = await setup_connect(hass, fake)
    result = await open_options(hass, entry, "dashboard")
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "dashboard"  # asks first
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "dashboard_failed"
    assert result["description_placeholders"]["help"].endswith("#build-the-dashboard-yourself")
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_dashboard_from_the_options(hass, fake):
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "lovelace", {})
    entry = await setup_connect(hass, fake)
    result = await open_options(hass, entry, "dashboard")
    result = await hass.config_entries.options.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "dashboard_created"
    assert "texecom-alarm" in hass.data["lovelace"].dashboards
    assert await hass.config_entries.async_unload(entry.entry_id)
