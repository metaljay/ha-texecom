"""Options, diagnostics, Repairs notices and the dashboard."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.texecom.dashboard import async_create_dashboard, build_config
from custom_components.texecom.diagnostics import async_get_config_entry_diagnostics
from custom_components.texecom.repairs import async_create_fix_flow

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


async def test_diagnostics_hide_codes_and_address(hass, fake):
    # The panel keeps Home Assistant's local time (the test runs HA on US/Pacific).
    fake.clock_offset = dt_util.now().replace(tzinfo=None) - datetime.now()
    entry = await setup_connect(hass, fake, options={"alarm_code": "4321"})
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["data"]["udl"] == "**REDACTED**"
    assert diag["entry"]["data"]["host"] == "**REDACTED**"
    assert diag["entry"]["options"]["alarm_code"] == "**REDACTED**"
    assert diag["panel"]["connected"] is True and abs(diag["panel"]["panel_clock_drift_s"]) < 5
    assert len(diag["panel"]["zones"]) == 8
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_clock_repair_and_fix(hass, fake):
    fake.clock_offset = timedelta(days=-700)  # e.g. after a total power loss
    entry = await setup_connect(hass, fake)
    issue_id = f"panel_clock_{entry.entry_id}"
    issue = ir.async_get(hass).async_get_issue("texecom", issue_id)
    assert issue is not None and issue.is_fixable
    assert issue.translation_placeholders["drift"] == "about 2 years behind"

    flow = await async_create_fix_flow(hass, issue_id, issue.data)
    flow.hass = hass
    result = await flow.async_step_init()
    assert result["step_id"] == "confirm"
    await flow.async_step_confirm({})
    assert entry.options["time_sync"] is True
    await hass.async_block_till_done()
    await wait_for(lambda: entry.runtime_data.connected)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await wait_for(lambda: fake.clock_set_to is not None)  # the clock was set
    assert ir.async_get(hass).async_get_issue("texecom", issue_id) is None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_a_clock_with_an_impossible_date(hass, fake):
    fake.clock_raw = bytes([0, 0, 0, 0, 0, 0])  # day 0, month 0
    entry = await setup_connect(hass, fake)
    issue = ir.async_get(hass).async_get_issue("texecom", f"panel_clock_{entry.entry_id}")
    assert issue is not None and issue.translation_placeholders["drift"].startswith("not set")
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["panel"]["panel_clock"].startswith("unreadable")
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_offline_repair_notice(hass, fake):
    entry = await setup_connect(hass, fake)
    panel = entry.runtime_data
    await fake.close()
    await wait_for(lambda: not panel.connected)
    panel.disconnected_since = time.monotonic() - 16 * 60
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=2))
    await hass.async_block_till_done()
    issue = ir.async_get(hass).async_get_issue("texecom", f"panel_offline_{entry.entry_id}")
    assert issue is not None and issue.translation_placeholders["minutes"] == "16"
    panel.set_connected(True)  # back again
    assert ir.async_get(hass).async_get_issue("texecom", f"panel_offline_{entry.entry_id}") is None
    panel.set_connected(False)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_dashboard_layout(hass, fake):
    entry = await setup_connect(hass, fake)
    config = build_config(hass, entry)
    left, right = config["views"][0]["sections"]
    alarm = next(c for c in left["cards"] if c.get("entity") == "alarm_control_panel.texecom_house")
    assert alarm["features"][0]["modes"] == ["armed_away", "armed_night", "disarmed"]
    headings = [c["heading"] for c in right["cards"] if c["type"] == "heading"]
    assert headings == ["Doors and windows", "Motion", "Fire and safety"]
    tiles = [c["entity"] for c in right["cards"] if c["type"] == "tile"]
    assert tiles[:2] == ["binary_sensor.texecom_front_door", "binary_sensor.texecom_patio_door"]
    panel_tiles = [c.get("name") for c in left["cards"] if c["type"] == "tile" and "name" in c]
    assert panel_tiles == ["Connection", "Mains", "Faults", "Tamper", "Keypad", "Battery"]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_dashboard_without_lovelace_fails_gently(hass, fake):
    entry = await setup_connect(hass, fake)
    result = await options(hass, entry, create_dashboard=True)
    assert result["errors"] == {"base": "dashboard_failed"}
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_dashboard_is_created(hass, fake):
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "lovelace", {})
    entry = await setup_connect(hass, fake)
    url = await async_create_dashboard(hass, entry)
    assert url == "texecom-alarm"
    saved = await hass.data["lovelace"].dashboards[url].async_load(False)
    assert saved["views"][0]["title"] == "Alarm"
    assert await hass.config_entries.async_unload(entry.entry_id)
