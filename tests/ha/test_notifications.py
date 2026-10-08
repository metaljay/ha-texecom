"""The "Alarm not set" notification (notifications.py)."""

from __future__ import annotations

from homeassistant.components import persistent_notification

from .common import setup_connect, wait_for


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


def _notice(hass, kind: str, entry) -> dict | None:
    return persistent_notification._async_get_or_create_notifications(hass).get(f"texecom_{kind}_{entry.entry_id}")


async def test_mains_notice_while_on_battery(hass, fake):
    entry = await setup_connect(hass, fake)
    assert _notice(hass, "mains", entry) is None
    fake.on_battery = True
    await entry.runtime_data.read_power()
    await hass.async_block_till_done()
    note = _notice(hass, "mains", entry)
    assert note is not None and note["title"] == "Alarm panel on battery"
    await entry.runtime_data.read_power()  # still on battery: the same notice, not a new one
    fake.on_battery = False
    await entry.runtime_data.read_power()
    await hass.async_block_till_done()
    assert _notice(hass, "mains", entry) is None  # gone once the mains is back
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_tamper_notice_says_what_to_check(hass, fake):
    entry = await setup_connect(hass, fake)
    fake.send_log(60, 11, 0, areas=0)  # the panel's lid
    fake.send_log(62, 11, 0, areas=0)  # a detector's cover (the shared tamper circuit)
    await wait_for(lambda: "Auxiliary Tamper" in (_notice(hass, "tamper", entry) or {}).get("message", ""))
    note = _notice(hass, "tamper", entry)
    assert note["title"] == "Alarm tamper"
    assert "the panel's lid is open" in note["message"] and "a detector's cover is open" in note["message"]
    fake.send_log(60, 12, 0, areas=0)
    await wait_for(lambda: "Panel Box Tamper" not in _notice(hass, "tamper", entry)["message"])
    fake.send_log(62, 12, 0, areas=0)
    await wait_for(lambda: _notice(hass, "tamper", entry) is None)
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_notices_can_be_turned_off(hass, fake):
    entry = await setup_connect(hass, fake, options={"notify_mains": False, "notify_tamper": False})
    fake.on_battery = True
    await entry.runtime_data.read_power()
    fake.send_log(60, 11, 0, areas=0)
    await wait_for(lambda: hass.states.get("binary_sensor.texecom_tamper").state == "on")
    assert hass.states.get("binary_sensor.texecom_mains_power").state == "off"
    assert _notice(hass, "mains", entry) is None and _notice(hass, "tamper", entry) is None
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_notices_go_when_the_integration_unloads(hass, fake):
    entry = await setup_connect(hass, fake)
    fake.send_log(60, 11, 0, areas=0)
    await wait_for(lambda: _notice(hass, "tamper", entry) is not None)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert _notice(hass, "tamper", entry) is None
