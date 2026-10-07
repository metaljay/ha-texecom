"""The arm modes step both protocols' setup ends on (config_flow.py). The
setup screens themselves are in test_flows_*.py."""

from __future__ import annotations

from .common import UDL, after_progress, start


async def test_night_and_home_cannot_share_a_part_arm(hass, fake, no_setup):
    result = await start(hass, "connect")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"host": "127.0.0.1", "port": fake.port, "udl": UDL}
    )
    result = await after_progress(hass, result)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"night_part_arm": "1", "home_part_arm": "1", "create_dashboard": False}
    )
    assert result["errors"] == {"base": "same_part_arm"}
