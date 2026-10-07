"""Arming, disarming, codes, who changed it, failed arms and alarms."""

from __future__ import annotations

import pytest
from homeassistant.components import persistent_notification
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.event import async_track_state_change_event

from custom_components.texecom.connect import protocol as P

from .common import setup_connect, wait_for

ALARM = "alarm_control_panel.texecom_house"


def state(hass) -> str:
    return hass.states.get(ALARM).state


async def call(hass, service: str, code: str | None = None) -> None:
    data = {"entity_id": ALARM}
    if code is not None:
        data["code"] = code
    await hass.services.async_call("alarm_control_panel", service, data, blocking=True)


async def test_arm_night_away_and_disarm(hass, fake):
    entry = await setup_connect(hass, fake)
    await call(hass, "alarm_arm_night")
    assert (P.CMD_ARM_AREA, bytes([1, 1])) in fake.commands  # Part Arm 1, area 1
    await wait_for(lambda: state(hass) == "armed_night")
    attrs = hass.states.get(ALARM).attributes
    assert attrs["part_arm"] == 1 and attrs["changed_by"] == "Home Assistant"

    seen: list[str] = []

    @callback
    def record(event):
        seen.append(event.data["new_state"].state)

    unsub = async_track_state_change_event(hass, [ALARM], record)
    await call(hass, "alarm_arm_away")
    await wait_for(lambda: state(hass) == "armed_away")
    unsub()
    assert "disarmed" not in seen, seen  # a mode switch never flashes Disarmed

    await call(hass, "alarm_disarm")
    await wait_for(lambda: state(hass) == "disarmed")
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_home_is_hidden_when_not_used(hass, fake):
    entry = await setup_connect(hass, fake)
    with pytest.raises(HomeAssistantError):
        await call(hass, "alarm_arm_home")
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_alarm_code(hass, fake):
    entry = await setup_connect(hass, fake, options={"alarm_code": "4321", "code_arm_required": False})
    await call(hass, "alarm_arm_away")  # arming doesn't need the code
    await wait_for(lambda: state(hass) == "armed_away")
    with pytest.raises(ServiceValidationError):
        await call(hass, "alarm_disarm")
    with pytest.raises(ServiceValidationError):
        await call(hass, "alarm_disarm", code="1111")
    await call(hass, "alarm_disarm", code="4321")
    await wait_for(lambda: state(hass) == "disarmed")
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_alarm_code_for_arming_too(hass, fake):
    entry = await setup_connect(hass, fake, options={"alarm_code": "4321", "code_arm_required": True})
    with pytest.raises(ServiceValidationError):
        await call(hass, "alarm_arm_away")
    await call(hass, "alarm_arm_away", code="4321")
    await wait_for(lambda: state(hass) == "armed_away")
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_keypad_arm_shows_the_user_and_alarm_names_the_zone(hass, fake):
    entry = await setup_connect(hass, fake)
    fake.send_user(3)
    fake.set_area(3)  # keypad full arm
    await wait_for(lambda: state(hass) == "armed_away")
    assert hass.states.get(ALARM).attributes["changed_by"] == "User 3"

    fake.set_area(5)  # alarm...
    fake.set_zone(4, 0x11)  # ...set off by Kitchen (active + alarmed)
    await wait_for(lambda: state(hass) == "triggered")
    await wait_for(lambda: hass.states.get(ALARM).attributes["changed_by"] == "Kitchen")

    fake.set_area(0)  # disarmed, by nobody we know of (e.g. a fob)
    await wait_for(lambda: state(hass) == "disarmed")
    assert hass.states.get(ALARM).attributes["changed_by"] is None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_alarm_memory_doesnt_blame_a_zone_for_a_later_alarm(hass, fake):
    entry = await setup_connect(hass, fake)
    fake.set_zone(4, 0x11)  # Kitchen active, alarm memory still set, while disarmed
    await wait_for(lambda: hass.states.get("binary_sensor.texecom_kitchen").state == "on")
    fake.set_area(3)
    await wait_for(lambda: state(hass) == "armed_away")
    fake.set_area(5)
    fake.set_zone(2, 0x11)  # the Hallway sets it off
    await wait_for(lambda: hass.states.get(ALARM).attributes["changed_by"] == "Hallway")
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_failed_arm_is_explained(hass, fake):
    entry = await setup_connect(hass, fake)
    fake.send_log(85, 0, 4)  # ARM_FAILED: Kitchen active at the end of the exit time
    fake.send_log(85, 0, 2)  # ...and the Hallway
    await wait_for(lambda: len(_notes(hass)) == 1 and "Hallway" in next(iter(_notes(hass).values()))["message"])
    note = next(iter(_notes(hass).values()))
    assert "Kitchen" in note["message"] and note["title"] == "Alarm not set"
    fake.set_area(3)  # armed on the next try: the notice goes
    await wait_for(lambda: not _notes(hass))
    assert await hass.config_entries.async_unload(entry.entry_id)


def _notes(hass):
    return {
        key: value
        for key, value in persistent_notification._async_get_or_create_notifications(hass).items()
        if key.startswith("texecom_arm_failed")
    }


async def test_stays_available_while_the_smartcom_reports_an_alarm(hass, fake):
    entry = await setup_connect(hass, fake)
    panel = entry.runtime_data
    fake.set_area(3)
    await wait_for(lambda: state(hass) == "armed_away")
    panel.offline_grace = 0.5
    await fake.close()  # the SmartCom stops answering
    await wait_for(lambda: hass.states.get("binary_sensor.texecom_panel_connection").state == "off")
    assert state(hass) == "armed_away"  # last known state, not "unavailable"
    await wait_for(lambda: state(hass) == "unavailable", timeout=3)  # gone for too long
    assert await hass.config_entries.async_unload(entry.entry_id)
