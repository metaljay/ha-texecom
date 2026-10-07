"""The fix for a wrong panel clock (repairs.py)."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.texecom.repairs import async_create_fix_flow

from .common import setup_connect, wait_for


async def test_clock_repair_and_fix(hass, fake):
    fake.clock_offset = timedelta(days=-700)  # e.g. after a total power loss
    entry = await setup_connect(hass, fake)
    issue_id = f"panel_clock_{entry.entry_id}"
    issue = ir.async_get(hass).async_get_issue("texecom", issue_id)
    assert issue is not None and issue.is_fixable
    assert issue.translation_placeholders["drift"] == "about 2 years behind"

    flow = await async_create_fix_flow(hass, issue_id, issue.data)
    flow.hass = hass
    result = await flow.async_step_init()
    assert result["step_id"] == "confirm"
    await flow.async_step_confirm({})
    assert entry.options["time_sync"] is True
    await hass.async_block_till_done()
    await wait_for(lambda: entry.runtime_data.connected)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await wait_for(lambda: fake.clock_set_to is not None)  # the clock was set
    assert ir.async_get(hass).async_get_issue("texecom", issue_id) is None
    assert await hass.config_entries.async_unload(entry.entry_id)
