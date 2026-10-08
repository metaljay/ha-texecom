"""The Alarm dashboard (dashboard.py)."""

from __future__ import annotations

from custom_components.texecom.dashboard import async_create_dashboard, build_config

from .common import setup_connect


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
    named_tiles = [c.get("name") for c in left["cards"] if c["type"] == "tile" and "name" in c]
    assert named_tiles == ["Ready to arm", "Connection", "Mains", "Faults", "Tamper", "Keypad", "Battery"]
    activity = next(c for c in left["cards"] if c["type"] == "logbook")
    assert activity["target"]["entity_id"] == ["alarm_control_panel.texecom_house"]  # not every door opening
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
