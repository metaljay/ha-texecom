"""Activity-list lines for the panel's events (logbook.py)."""

from __future__ import annotations

from homeassistant.core import Event

from custom_components.texecom import logbook
from custom_components.texecom.const import DOMAIN, EVENT

from .common import ALARM, setup_connect


def describer(hass):
    """The function Home Assistant's logbook would call for texecom_event."""
    found = {}
    logbook.async_describe_events(hass, lambda domain, event, describe: found.setdefault((domain, event), describe))
    return found[(DOMAIN, EVENT)]


async def test_each_event_reads_as_a_sentence(hass, fake):
    entry = await setup_connect(hass, fake, options={"user_names": {"3": "Sam"}})
    describe = describer(hass)

    def line(**data):
        return describe(Event(EVENT, {"entry_id": entry.entry_id, **data}))

    assert line(type="zone_alarm", zone=4, zone_name="Kitchen", tamper=False) == {
        "name": "House Alarm",
        "message": "was set off by Kitchen",
        "entity_id": ALARM,  # so it shows in the alarm's activity, and on the Alarm dashboard
    }
    assert line(type="zone_alarm", zone=4, zone_name="Kitchen", tamper=True)["message"] == (
        "was set off by a tamper on Kitchen"
    )
    assert line(type="arm_failed", zone=4, zone_name="Kitchen", areas=1)["message"] == (
        "didn't arm: Kitchen was active when the exit time ended"
    )
    assert line(type="tamper", source="Panel Box Tamper", log_type=60)["message"] == (
        "reported a tamper: Panel Box Tamper"
    )
    # Stored event-log entries (Crestron reads them through Texecom Connect)
    log = {"group": 6, "parameter": 0, "areas": 1, "communicated": False, "panel_time": "2026-10-18T13:10:22"}
    assert line(type="log", log_type=208, name="Remote Part Arm 2", index=713, **log) == {
        "name": "House Alarm",
        "message": "panel log: Remote Part Arm 2",
        "entity_id": ALARM,  # its area bitmap says area 1
    }
    assert line(type="log", log_type=3, name="Interior", index=716, **{**log, "parameter": 51})["message"] == (
        "panel log: Interior (zone 51)"
    )
    assert line(type="tamper_cleared", source="Panel Box Tamper", log_type=60)["message"] == (
        "tamper put right: Panel Box Tamper"
    )
    assert line(type="fault", source="AC Fail", log_type=47)["message"] == "reported a fault: AC Fail"
    assert line(type="fault_cleared", source="AC Fail", log_type=None)["message"] == "fault cleared: AC Fail"
    assert line(type="user", user=3, method="code")["message"] == "keypad used by Sam (a code)"
    assert line(type="user", user=5, method="tag")["message"] == "keypad used by User 5 (a tag)"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_an_event_from_a_removed_panel(hass):
    line = describer(hass)(Event(EVENT, {"entry_id": "gone", "type": "fault", "source": "AC Fail"}))
    assert line == {"name": "Texecom alarm", "message": "reported a fault: AC Fail", "entity_id": None}
