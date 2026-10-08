"""Setting up an entry (__init__.py and factory.py): the devices, entities
and names each kind of entry gets, and a Crestron entry working end to end."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

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
    # Zone tamper sensors exist but are off by default.
    tamper = er.async_get(hass).async_get("binary_sensor.texecom_front_door_tamper")
    assert tamper is not None and tamper.disabled_by is not None

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


async def setup_crestron(hass, number: int, udl: str | None = UDL):
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
        options={"night_part_arm": 1, "home_part_arm": 0, "keypad_arm_mode": "away", "status_poll": 60},
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
