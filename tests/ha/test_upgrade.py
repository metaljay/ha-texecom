"""Upgrading: a setup saved by an older release still works, unchanged.

The data and options below are frozen exactly as v0.1.0 (the first release)
saved them. Never edit them to match new code: if a test here fails, the new
code would break existing users' setups, and needs `async_migrate_entry`.
Add a new frozen set whenever a release changes what it saves.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.texecom.config_flow import TexecomConfigFlow
from custom_components.texecom.const import DOMAIN

from .common import UDL, wait_for

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from test_crestron import FakeCrestronPort  # noqa: E402

# ─── Saved by v0.1.0 ────────────────────────────────────────────────────────

V0_1_0_CONNECT_DATA = {
    "protocol": "connect",
    "host": "127.0.0.1",
    "udl": UDL,
    "info": {"model": "Premier Elite", "zones": 24, "firmware": "V6.05.03LS1"},
    "zones": [
        {"number": 1, "name": "Front Door", "type": 1, "areas": [1]},
        {"number": 2, "name": "Hallway", "type": 1, "areas": [1]},
        {"number": 3, "name": "Lounge", "type": 3, "areas": [1]},
        {"number": 4, "name": "Kitchen", "type": 3, "areas": [1]},
        {"number": 5, "name": "Patio Door", "type": 4, "areas": [1]},
        {"number": 6, "name": "Garage", "type": 3, "areas": [1]},
        {"number": 7, "name": "Landing", "type": 3, "areas": [1]},
        {"number": 8, "name": "Smoke Detector", "type": 9, "areas": [1]},
    ],
    "areas": [{"number": 1, "name": "HOUSE"}],
}
# As v0.1.0's options screen saved them (it replaced, not merged, the options).
V0_1_0_CONNECT_OPTIONS = {
    "night_part_arm": 1,
    "home_part_arm": 0,
    "keypad_arm_mode": "away",
    "alarm_code": None,
    "code_arm_required": False,
    "time_sync": True,
}
V0_1_0_CRESTRON_DATA = {
    "protocol": "crestron",
    "connection": "network",
    "host": "127.0.0.1",
    "udl": None,
    "zone_count": 3,
    "area_count": 1,
}
V0_1_0_CRESTRON_OPTIONS = {
    "night_part_arm": 0,
    "home_part_arm": 0,
    "alarm_code": None,
    "code_arm_required": False,
    "status_poll": 60,
}

# Entity keys v0.1.0 created (unique ID = "<entry_id>_<key>"), with the entity
# IDs it gave them. One is renamed, as a user might have done.
V0_1_0_CONNECT_ENTITIES = {
    ("binary_sensor", "zone_1"): "binary_sensor.my_front_door",
    ("binary_sensor", "zone_1_tamper"): "binary_sensor.texecom_front_door_tamper",
    ("binary_sensor", "zone_8"): "binary_sensor.texecom_smoke_detector",
    ("binary_sensor", "connection"): "binary_sensor.texecom_panel_connection",
    ("sensor", "panel_voltage"): "sensor.texecom_panel_voltage",
    ("sensor", "battery_voltage"): "sensor.texecom_battery_voltage",
    ("alarm_control_panel", "area_1"): "alarm_control_panel.texecom_house",
}
V0_1_0_CRESTRON_ENTITIES = {
    ("binary_sensor", "zone_1"): "binary_sensor.texecom_zone_1",
    ("binary_sensor", "zone_3"): "binary_sensor.texecom_zone_3",
    ("binary_sensor", "connection"): "binary_sensor.texecom_panel_connection",
    ("alarm_control_panel", "area_1"): "alarm_control_panel.texecom_area_a",
}


# ─── Helpers ────────────────────────────────────────────────────────────────


def register_old_entities(hass, entry: MockConfigEntry, entities: dict) -> None:
    registry = er.async_get(hass)
    for (platform, key), entity_id in entities.items():
        registry.async_get_or_create(
            platform,
            DOMAIN,
            f"{entry.entry_id}_{key}",
            config_entry=entry,
            suggested_object_id=entity_id.split(".", 1)[1],
        )
        assert registry.async_get(entity_id), entity_id


def assert_entities_kept(hass, entry: MockConfigEntry, entities: dict) -> None:
    registry = er.async_get(hass)
    for (platform, key), entity_id in entities.items():
        assert registry.async_get_entity_id(platform, DOMAIN, f"{entry.entry_id}_{key}") == entity_id
        state = hass.states.get(entity_id)
        assert state is not None and state.state != "unavailable", entity_id
    ids = [e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)]
    assert not [i for i in entities.values() if f"{i}_2" in ids], ids  # no duplicates


async def assert_options_screens_open(hass, entry: MockConfigEntry, steps: list[str]) -> None:
    for step in steps:
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] is FlowResultType.MENU
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"next_step_id": step})
        assert result["type"] is FlowResultType.FORM, (step, result)
        hass.config_entries.options.async_abort(result["flow_id"])


# ─── Tests ──────────────────────────────────────────────────────────────────


async def test_a_v0_1_0_connect_setup_loads_unchanged(hass, fake):
    data = {**V0_1_0_CONNECT_DATA, "port": fake.port}
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Texecom Premier Elite 24",
        unique_id=f"127.0.0.1:{fake.port}",
        data=data,
        options=V0_1_0_CONNECT_OPTIONS,
    )
    entry.add_to_hass(hass)
    register_old_entities(hass, entry, V0_1_0_CONNECT_ENTITIES)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await wait_for(lambda: entry.runtime_data.connected)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.version == 1
    assert dict(entry.data) == data
    assert dict(entry.options) == V0_1_0_CONNECT_OPTIONS
    assert_entities_kept(hass, entry, V0_1_0_CONNECT_ENTITIES)
    assert hass.states.get("binary_sensor.my_front_door").attributes["friendly_name"] == "Front Door"
    await assert_options_screens_open(hass, entry, ["arm_modes", "alarm_code", "user_names", "notices", "clock"])
    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.fixture
async def port(socket_enabled):
    fake = FakeCrestronPort()
    number = await fake.start()
    yield number
    await fake.close()


async def test_a_v0_1_0_crestron_setup_loads_unchanged(hass, port):
    data = {**V0_1_0_CRESTRON_DATA, "port": port}
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Texecom Premier Elite",
        unique_id=f"127.0.0.1:{port}",
        data=data,
        options=V0_1_0_CRESTRON_OPTIONS,
    )
    entry.add_to_hass(hass)
    register_old_entities(hass, entry, V0_1_0_CRESTRON_ENTITIES)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    alarm = "alarm_control_panel.texecom_area_a"
    await wait_for(lambda: hass.states.get(alarm) and hass.states.get(alarm).state == "disarmed")

    assert entry.state is ConfigEntryState.LOADED
    assert dict(entry.data) == data
    assert dict(entry.options) == V0_1_0_CRESTRON_OPTIONS
    assert_entities_kept(hass, entry, V0_1_0_CRESTRON_ENTITIES)
    await assert_options_screens_open(hass, entry, ["arm_modes", "alarm_code", "status_poll"])
    assert await hass.config_entries.async_unload(entry.entry_id)


# ─── Saved by v0.2.10 (and earlier): what's turned on when upgrading ────────

# Options as a v0.2.10 setup saved them when the clock screen was never opened.
V0_2_10_CONNECT_OPTIONS = {"night_part_arm": 1, "home_part_arm": 0, "user_names": {"3": "Sam"}}


async def test_upgrading_turns_on_what_new_setups_get(hass, fake):
    """Entries made before 0.2.11 get what new setups start with: the sensors
    that were off by default, and Keep the panel clock right. What the user
    turned off themselves stays off."""
    data = {**V0_1_0_CONNECT_DATA, "port": fake.port}
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Texecom Premier Elite 24",
        unique_id=f"127.0.0.1:{fake.port}",
        data=data,
        options=V0_2_10_CONNECT_OPTIONS,
        version=1,
        minor_version=1,
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    off_by_default = registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        f"{entry.entry_id}_zone_1_tamper",
        config_entry=entry,
        suggested_object_id="texecom_front_door_tamper",
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )
    off_by_user = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.entry_id}_battery_current",
        config_entry=entry,
        suggested_object_id="texecom_battery_current",
        disabled_by=er.RegistryEntryDisabler.USER,
    )

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await wait_for(lambda: entry.runtime_data.connected)

    assert entry.minor_version == TexecomConfigFlow.MINOR_VERSION
    assert dict(entry.data) == data
    # Keep the panel clock right writes to the panel: never switched on unasked.
    assert dict(entry.options) == V0_2_10_CONNECT_OPTIONS
    assert registry.async_get(off_by_default.entity_id).disabled_by is None
    assert registry.async_get(off_by_user.entity_id).disabled_by is er.RegistryEntryDisabler.USER
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_upgrading_keeps_a_clock_turned_off(hass, fake):
    options = {**V0_2_10_CONNECT_OPTIONS, "time_sync": False}
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"127.0.0.1:{fake.port}",
        data={**V0_1_0_CONNECT_DATA, "port": fake.port},
        options=options,
        minor_version=1,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.minor_version == TexecomConfigFlow.MINOR_VERSION and dict(entry.options) == options
    assert await hass.config_entries.async_unload(entry.entry_id)
