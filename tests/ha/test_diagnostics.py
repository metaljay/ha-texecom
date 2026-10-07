"""The diagnostics download (diagnostics.py)."""

from __future__ import annotations

from datetime import datetime

from homeassistant.util import dt as dt_util

from custom_components.texecom.diagnostics import async_get_config_entry_diagnostics

from .common import setup_connect


async def test_diagnostics_hide_codes_and_address(hass, fake):
    # The panel keeps Home Assistant's local time (the test runs HA on US/Pacific).
    fake.clock_offset = dt_util.now().replace(tzinfo=None) - datetime.now()
    entry = await setup_connect(hass, fake, options={"alarm_code": "4321"})
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["data"]["udl"] == "**REDACTED**"
    assert diag["entry"]["data"]["host"] == "**REDACTED**"
    assert diag["entry"]["options"]["alarm_code"] == "**REDACTED**"
    assert diag["panel"]["connected"] is True and abs(diag["panel"]["panel_clock_drift_s"]) < 5
    assert len(diag["panel"]["zones"]) == 8
    assert await hass.config_entries.async_unload(entry.entry_id)
