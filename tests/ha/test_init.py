"""Setting up an entry (__init__.py and factory.py): the devices, entities
and names each kind of entry gets, and a Crestron entry working end to end."""

from __future__ import annotations

import asyncio
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.texecom.const import DOMAIN
from custom_components.texecom.crestron import protocol as CP

from .common import UDL, setup_connect, wait_for

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from test_crestron import FakeCrestronPort  # noqa: E402


async def test_entities_devices_and_states(hass, fake):
    entry = await setup_connect(hass, fake)

    alarm = hass.states.get("alarm_control_panel.texecom_house")
    assert alarm is not None and alarm.state == "disarmed"
    assert alarm.attributes["friendly_name"] == "House Alarm"
    assert alarm.attributes["supported_features"] == 2 | 4  # away + night (home not used)

    front = hass.states.get("binary_sensor.texecom_front_door")
    assert front.state == "off" and front.attributes["device_class"] == "door"
    assert hass.states.get("binary_sensor.texecom_smoke_detector").attributes["device_class"] == "smoke"
    assert hass.states.get("binary_sensor.texecom_hallway").attributes["device_class"] == "motion"
    assert hass.states.get("binary_sensor.texecom_panel_connection").state == "on"
    assert hass.states.get("binary_sensor.texecom_mains_power").state == "on"
    assert hass.states.get("binary_sensor.texecom_tamper").state == "off"
    assert hass.states.get("binary_sensor.texecom_problem").state == "off"
    assert hass.states.get("sensor.texecom_keypad_display").state == "Premier Elite"
    assert float(hass.states.get("sensor.texecom_panel_voltage").state) > 13

    devices = {d.name for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)}
    assert {"Premier Elite 24 panel", "House Alarm", "Front Door", "Smoke Detector"} <= devices
    # Everything is on to start with: zone tamper sensors and current readings too.
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert [e.entity_id for e in entities if e.disabled_by] == []
    assert hass.states.get("binary_sensor.texecom_front_door_tamper").state == "off"
    assert hass.states.get("sensor.texecom_battery_current") is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_only_old_zones_can_be_deleted(hass, fake):
    from custom_components.texecom import async_remove_config_entry_device
    from custom_components.texecom.const import DOMAIN

    entry = await setup_connect(hass, fake)
    devices = dr.async_get(hass)
    # A zone the installer has since removed from the panel.
    old = devices.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"{entry.entry_id}_zone_99")}, name="Old Zone"
    )
    by_name = {d.name: d for d in dr.async_entries_for_config_entry(devices, entry.entry_id)}
    assert await async_remove_config_entry_device(hass, entry, old) is True
    for current in ("Front Door", "House Alarm", "Premier Elite 24 panel"):
        assert await async_remove_config_entry_device(hass, entry, by_name[current]) is False
    assert await hass.config_entries.async_unload(entry.entry_id)


# ─── A Crestron entry ───────────────────────────────────────────────────────

ALARM = "alarm_control_panel.texecom_area_a"


@pytest.fixture
async def port(socket_enabled):
    fake = FakeCrestronPort()
    number = await fake.start()
    yield fake, number
    await fake.close()


async def setup_crestron(hass, number: int, udl: str | None = UDL, **options):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Texecom Premier Elite",
        unique_id=f"127.0.0.1:{number}",
        data={
            "protocol": "crestron",
            "connection": "network",
            "host": "127.0.0.1",
            "port": number,
            "udl": udl,
            "zone_count": 3,
            "area_count": 1,
        },
        options={"night_part_arm": 1, "home_part_arm": 0, "keypad_arm_mode": "away", "status_poll": 60, **options},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await wait_for(lambda: hass.states.get(ALARM) and hass.states.get(ALARM).state == "disarmed")
    return entry


async def test_crestron_entities_and_keypad_events(hass, port):
    fake, number = port
    entry = await setup_crestron(hass, number)
    assert hass.states.get(ALARM).attributes["friendly_name"] == "Area A Alarm"
    assert hass.states.get("binary_sensor.texecom_zone_1").state == "off"
    assert hass.states.get("binary_sensor.texecom_tamper") is None  # Connect only
    assert hass.states.get("binary_sensor.texecom_area_a_ready_to_arm") is None  # Connect only

    fake.send('"Z0021')
    await wait_for(lambda: hass.states.get("binary_sensor.texecom_zone_2").state == "on")
    fake.send('"A0013')  # armed at the keypad by user 3
    await wait_for(lambda: hass.states.get(ALARM).state == "armed_away")
    assert hass.states.get(ALARM).attributes["changed_by"] == "User 3"
    fake.send('"D0013')
    await wait_for(lambda: hass.states.get(ALARM).state == "disarmed")
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_crestron_night_uses_the_binary_part_arm(hass, port):
    fake, number = port
    entry = await setup_crestron(hass, number)
    entry.runtime_data.blackout = 0.1
    await hass.services.async_call("alarm_control_panel", "alarm_arm_night", {"entity_id": ALARM}, blocking=True)
    await wait_for(lambda: fake.received[-1:] == [CP.WINTEX_LOGOUT])
    assert fake.received[-3:] == [b"W" + UDL.encode(), CP.part_arm_frame(1, 1), CP.WINTEX_LOGOUT]
    assert hass.states.get(ALARM).state == "arming"
    fake.send('"A0010')  # the held-back event
    await wait_for(lambda: hass.states.get(ALARM).state == "armed_night")
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_crestron_without_udl_is_sensors_only(hass, port):
    from homeassistant.exceptions import HomeAssistantError

    _fake, number = port
    entry = await setup_crestron(hass, number, udl=None)
    assert hass.states.get(ALARM).attributes["supported_features"] == 0  # no arm buttons
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("alarm_control_panel", "alarm_arm_away", {"entity_id": ALARM}, blocking=True)
    assert await hass.config_entries.async_unload(entry.entry_id)


POWER_SENSORS = [
    "sensor.texecom_panel_voltage",
    "sensor.texecom_battery_voltage",
    "sensor.texecom_panel_current",
    "sensor.texecom_battery_current",
]


async def test_crestron_power_sensors_come_with_the_readings_option(hass, port):
    """Crestron reads power through Texecom Connect in a UDL session: the
    sensors exist only with the UDL code and the readings switched on."""
    _fake, number = port
    entry = await setup_crestron(hass, number, session_reads=True)
    assert all(hass.states.get(e) for e in POWER_SENSORS)
    assert hass.states.get("sensor.texecom_panel_voltage").state == "unavailable"  # until the first reading
    assert entry.runtime_data.session_reads is True
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_the_event_log_carries_on_from_where_it_was_left_after_a_reload(hass, port, hass_storage):
    """A reload (or restart) carries on reading the panel's event log from
    where it was, instead of skipping what the panel logged in between."""
    _fake, number = port
    entry = await setup_crestron(hass, number)
    assert entry.runtime_data.log_pointer is None
    entry.runtime_data.resume_log_from(41)  # as a reading would leave it
    entry.runtime_data.notify()
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.log_pointer == 41
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()
    assert hass_storage[f"texecom.{entry.entry_id}.log_position"]["data"] == {"pointer": 41}


async def test_deleting_the_panel_deletes_its_log_position(hass, port, hass_storage):
    _fake, number = port
    entry = await setup_crestron(hass, number)
    entry.runtime_data.resume_log_from(7)
    entry.runtime_data.notify()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()
    assert f"texecom.{entry.entry_id}.log_position" in hass_storage
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert f"texecom.{entry.entry_id}.log_position" not in hass_storage


async def test_crestron_has_no_power_sensors_without_the_option_or_the_udl_code(hass, port):
    _fake, number = port
    for kwargs in ({}, {"udl": None, "session_reads": True}):
        entry = await setup_crestron(hass, number, **kwargs)
        assert not any(hass.states.get(e) for e in POWER_SENSORS), kwargs
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()


async def test_the_panel_clock_uses_home_assistants_time_zone(hass, fake):
    """The driver gets Home Assistant's own time zone object rather than making
    one: making one can read the time zone database from disk, which holds up
    Home Assistant while it does."""
    import zoneinfo

    from homeassistant.util import dt as dt_util

    zoneinfo.ZoneInfo.clear_cache(only_keys=[hass.config.time_zone])  # as if not read yet
    entry = await setup_connect(hass, fake)
    assert entry.runtime_data.time_zone is dt_util.get_default_time_zone()
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_stopping_home_assistant_closes_the_session(hass, fake):
    """Home Assistant doesn't unload integrations when it stops, so the
    integration closes its session and stops reconnecting itself, rather than
    being cut off at the very end."""
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP

    entry = await setup_connect(hass, fake)
    connections = fake.connections
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    await wait_for(lambda: not fake.writers)
    await asyncio.sleep(0.3)
    assert fake.connections == connections  # no reconnect either
    assert not entry.runtime_data.connected
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_a_failed_setup_leaves_no_connection_open(hass, fake):
    """If setting up fails (here: building the dashboard, which uses Home
    Assistant's internals), no connection to the panel is left running: it
    would hold the SmartCom's only session."""
    from unittest.mock import patch

    from .common import layout_of

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"protocol": "connect", "host": "127.0.0.1", "port": fake.port, "udl": UDL, "create_dashboard": True}
        | (await layout_of(fake)),
        options={"night_part_arm": 1},
    )
    entry.add_to_hass(hass)
    connections = fake.connections
    with patch("custom_components.texecom.async_create_dashboard", side_effect=RuntimeError("changed internals")):
        assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await asyncio.sleep(0.3)
    assert fake.connections == connections
