"""The Alarm dashboard (dashboard.py)."""

from __future__ import annotations

from unittest.mock import patch

from fake_connect_panel import DEMO_ZONES, FakeConnectPanel
from homeassistant.config_entries import ConfigEntryState

from custom_components.texecom.dashboard import async_create_dashboard, build_config

from .common import ALARM, setup_connect, wait_for


async def test_dashboard_layout(hass, fake):
    """In the order a phone shows it: the alarm, the zones, activity, the panel."""
    entry = await setup_connect(hass, fake)
    config = build_config(hass, entry)
    alarm, zones, activity, panel = (section["cards"] for section in config["views"][0]["sections"])
    assert [c["heading"] for c in alarm + activity + panel if c["type"] == "heading"] == ["Alarm", "Activity", "Panel"]
    card = next(c for c in alarm if c.get("entity") == ALARM)
    # Buttons with words on them, only the ones this alarm offers (Night is
    # mapped to a part arm in these tests, Home isn't).
    assert card["type"] == "alarm-panel"
    assert card["states"] == ["arm_away", "arm_night"]
    headings = [c["heading"] for c in zones if c["type"] == "heading"]
    assert headings == ["Doors and windows", "Motion", "Fire and safety"]
    tiles = [c["entity"] for c in zones if c["type"] == "tile"]
    assert tiles[:2] == ["binary_sensor.texecom_front_door", "binary_sensor.texecom_patio_door"]
    assert [c.get("name") for c in alarm if c["type"] == "tile"] == ["Ready to arm"]
    names = [c.get("name") for c in panel if c["type"] == "tile"]
    assert names == ["Connection", "Mains power", "Faults", "Tamper", "Battery", "Keypad shows"]
    # Green when all is well (Home Assistant's amber reads as a warning).
    green = [c["name"] for c in alarm + panel if c.get("color") == "green"]
    assert green == ["Ready to arm", "Connection", "Mains power"]
    logbook = next(c for c in activity if c["type"] == "logbook")
    assert logbook["target"]["entity_id"] == [ALARM]  # not every door opening
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_dashboard_says_when_the_panel_cant_be_reached(hass, fake):
    """Otherwise every tile just says Unavailable."""
    entry = await setup_connect(hass, fake)
    alarm = build_config(hass, entry)["views"][0]["sections"][0]["cards"]
    notice = next(c for c in alarm if c["type"] == "conditional")
    assert notice["conditions"] == [
        {"condition": "state", "entity": "binary_sensor.texecom_panel_connection", "state": "off"}
    ]
    assert "can't reach the alarm panel" in notice["card"]["content"]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_dashboard_buttons_follow_arm_buttons(hass, fake):
    """The labelled card shows every button it's given, so saving Configure →
    Arm buttons updates it; a list the user rearranged is left alone."""
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "lovelace", {})
    entry = await setup_connect(hass, fake)
    url = await async_create_dashboard(hass, entry)
    dashboard = hass.data["lovelace"].dashboards[url]

    async def buttons() -> list[str]:
        cards = (await dashboard.async_load(False))["views"][0]["sections"][0]["cards"]
        return next(c for c in cards if c.get("entity") == ALARM)["states"]

    assert await buttons() == ["arm_away", "arm_night"]
    hass.config_entries.async_update_entry(entry, options={**entry.options, "home_part_arm": 2})  # reloads
    await hass.async_block_till_done()
    await wait_for(lambda: entry.state is ConfigEntryState.LOADED)
    assert await buttons() == ["arm_away", "arm_night", "arm_home"]

    config = await dashboard.async_load(False)
    card = next(c for c in config["views"][0]["sections"][0]["cards"] if c.get("entity") == ALARM)
    card["states"] = ["arm_night", "arm_away"]  # the user's own order
    await dashboard.async_save(config)
    hass.config_entries.async_update_entry(entry, options={**entry.options, "home_part_arm": 0})
    await hass.async_block_till_done()
    await wait_for(lambda: entry.state is ConfigEntryState.LOADED)
    assert await buttons() == ["arm_night", "arm_away"]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_upgrading_gives_an_unedited_dashboard_the_new_layout(hass, fake):
    """A dashboard exactly as 0.2.16 made it gets the new layout; one the
    user changed is kept."""
    from homeassistant.setup import async_setup_component

    from custom_components.texecom.config_flow import TexecomConfigFlow
    from custom_components.texecom.dashboard import _build_config_v1

    assert await async_setup_component(hass, "lovelace", {})
    entry = await setup_connect(hass, fake)
    url = await async_create_dashboard(hass, entry)
    dashboard = hass.data["lovelace"].dashboards[url]
    for edit, sections in ((None, 4), ({"type": "markdown", "content": "Mine"}, 2)):
        config = _build_config_v1(hass, entry)
        if edit:
            config["views"][0]["sections"][1]["cards"].append(edit)
        await dashboard.async_save(config)
        assert await hass.config_entries.async_unload(entry.entry_id)
        hass.config_entries.async_update_entry(entry, minor_version=3)

        assert await hass.config_entries.async_setup(entry.entry_id)  # upgrades first
        await hass.async_block_till_done()
        assert entry.minor_version == TexecomConfigFlow.MINOR_VERSION
        assert len((await dashboard.async_load(False))["views"][0]["sections"]) == sections
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


async def test_each_panel_keeps_its_own_dashboard(hass, fake):
    """With a second panel added, refreshing the first panel's dashboard (made
    when it was the only one) updates it rather than adding another."""
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "lovelace", {})
    second = FakeConnectPanel(zones=DEMO_ZONES, exit_delay=0.05)
    await second.start()
    try:
        first_entry = await setup_connect(hass, fake)
        assert await async_create_dashboard(hass, first_entry) == "texecom-alarm"
        second_entry = await setup_connect(hass, second)
        second_url = await async_create_dashboard(hass, second_entry)
        assert second_url == f"texecom-alarm-{second_entry.entry_id[-6:].lower()}"
        assert await async_create_dashboard(hass, first_entry) == "texecom-alarm"
        assert await async_create_dashboard(hass, second_entry) == second_url
        assert sorted(url for url in hass.data["lovelace"].dashboards if url) == ["texecom-alarm", second_url]
        for entry in (first_entry, second_entry):
            assert await hass.config_entries.async_unload(entry.entry_id)
    finally:
        await second.close()


async def test_deleting_a_panel_deletes_its_dashboard(hass, fake):
    """Otherwise an "Alarm" stays in the sidebar with every entity missing."""
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "lovelace", {})
    second = FakeConnectPanel(zones=DEMO_ZONES, exit_delay=0.05)
    await second.start()
    try:
        first_entry = await setup_connect(hass, fake)
        await async_create_dashboard(hass, first_entry)
        second_entry = await setup_connect(hass, second)
        assert await async_create_dashboard(hass, second_entry) != "texecom-alarm"
        assert await hass.config_entries.async_remove(second_entry.entry_id)
        await hass.async_block_till_done()
        assert sorted(url for url in hass.data["lovelace"].dashboards if url) == ["texecom-alarm"]
        assert await hass.config_entries.async_remove(first_entry.entry_id)
        await hass.async_block_till_done()
        assert [url for url in hass.data["lovelace"].dashboards if url] == []
    finally:
        await second.close()


async def test_upgrading_lets_an_older_dashboards_buttons_follow_arm_buttons(hass, fake):
    """Dashboards made before 0.2.12 listed only the modes the alarm had then.
    Upgrading lists every mode on the alarm's card, and keeps the user's edits."""
    from homeassistant.setup import async_setup_component

    from custom_components.texecom.config_flow import TexecomConfigFlow
    from custom_components.texecom.dashboard import MODES, _build_config_v1

    assert await async_setup_component(hass, "lovelace", {})
    entry = await setup_connect(hass, fake)
    url = await async_create_dashboard(hass, entry)
    dashboard = hass.data["lovelace"].dashboards[url]
    config = _build_config_v1(hass, entry)
    left, right = config["views"][0]["sections"]
    alarm = next(c for c in left["cards"] if c.get("entity") == "alarm_control_panel.texecom_house")
    alarm["features"][0]["modes"] = ["armed_away", "armed_night", "disarmed"]  # as 0.2.9 made it
    right["cards"].append({"type": "markdown", "content": "Mine"})  # an edit of the user's
    await dashboard.async_save(config)
    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.config_entries.async_update_entry(entry, minor_version=2)

    assert await hass.config_entries.async_setup(entry.entry_id)  # upgrades first
    await hass.async_block_till_done()
    assert entry.minor_version == TexecomConfigFlow.MINOR_VERSION
    left, right = (await dashboard.async_load(False))["views"][0]["sections"]
    alarm = next(c for c in left["cards"] if c.get("entity") == "alarm_control_panel.texecom_house")
    assert alarm["features"][0]["modes"] == MODES
    assert {"type": "markdown", "content": "Mine"} in right["cards"]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_whatever_goes_wrong_with_the_dashboard_the_alarm_still_loads(hass, fake):
    """Making the dashboard reaches into Home Assistant's dashboards, which can
    change in any release. Whatever goes wrong there, setup carries on without
    the dashboard, and doesn't try again at each start."""
    import voluptuous as vol
    from homeassistant.components.lovelace.dashboard import DashboardsCollection
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "lovelace", {})
    refused = vol.Invalid("Url path is already in use")
    with patch.object(DashboardsCollection, "async_create_item", side_effect=refused):
        entry = await setup_connect(hass, fake, data={"create_dashboard": True})
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(ALARM) is not None
    assert "create_dashboard" not in entry.data
    assert [url for url in hass.data["lovelace"].dashboards if url] == []
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_whatever_goes_wrong_with_the_dashboard_upgrading_carries_on(hass, fake):
    """Upgrading from before 0.2.12 updates the dashboard's buttons; if Home
    Assistant's dashboards won't take it, the upgrade and setup still finish."""
    from homeassistant.components.lovelace.dashboard import LovelaceStorage
    from homeassistant.setup import async_setup_component

    from custom_components.texecom.config_flow import TexecomConfigFlow
    from custom_components.texecom.dashboard import _build_config_v1

    assert await async_setup_component(hass, "lovelace", {})
    entry = await setup_connect(hass, fake)
    url = await async_create_dashboard(hass, entry)
    dashboard = hass.data["lovelace"].dashboards[url]
    config = _build_config_v1(hass, entry)
    alarm = next(c for c in config["views"][0]["sections"][0]["cards"] if c.get("entity") == ALARM)
    alarm["features"][0]["modes"] = ["armed_away", "disarmed"]  # as 0.2.9 made it
    await dashboard.async_save(config)
    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.config_entries.async_update_entry(entry, minor_version=2)

    with patch.object(LovelaceStorage, "async_save", side_effect=RuntimeError("changed internals")):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.minor_version == TexecomConfigFlow.MINOR_VERSION
    assert await hass.config_entries.async_unload(entry.entry_id)
