"""Repairs notices (issues.py): the panel clock, and the panel being
unreachable."""

from __future__ import annotations

import time
from datetime import timedelta

from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.texecom.diagnostics import async_get_config_entry_diagnostics

from .common import setup_connect, wait_for


async def test_a_clock_with_an_impossible_date(hass, fake):
    fake.clock_raw = bytes([0, 0, 0, 0, 0, 0])  # day 0, month 0
    entry = await setup_connect(hass, fake)
    issue = ir.async_get(hass).async_get_issue("texecom", f"panel_clock_{entry.entry_id}")
    assert issue is not None and issue.translation_placeholders["drift"].startswith("not set")
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["panel"]["panel_clock"].startswith("unreadable")
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_offline_repair_notice(hass, fake):
    entry = await setup_connect(hass, fake)
    panel = entry.runtime_data
    await fake.close()
    await wait_for(lambda: not panel.connected)
    panel.disconnected_since = time.monotonic() - 16 * 60
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=2))
    await hass.async_block_till_done()
    issue = ir.async_get(hass).async_get_issue("texecom", f"panel_offline_{entry.entry_id}")
    assert issue is not None and issue.translation_placeholders["minutes"] == "16"
    panel.set_connected(True)  # back again
    assert ir.async_get(hass).async_get_issue("texecom", f"panel_offline_{entry.entry_id}") is None
    panel.set_connected(False)
    assert await hass.config_entries.async_unload(entry.entry_id)
