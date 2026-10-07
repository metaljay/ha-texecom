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
