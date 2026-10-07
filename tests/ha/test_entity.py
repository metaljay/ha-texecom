"""What every entity shares (entity.py): through a short drop in the
connection it stays available, showing its last state."""

from __future__ import annotations

from .common import setup_connect, state, wait_for


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
