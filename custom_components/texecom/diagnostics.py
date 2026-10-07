"""Diagnostics download (the UDL code and alarm code are redacted)."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import TexecomConfigEntry
from .const import CONF_ALARM_CODE, CONF_UDL

TO_REDACT = {CONF_UDL, CONF_ALARM_CODE, CONF_HOST}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: TexecomConfigEntry) -> dict[str, Any]:
    panel = entry.runtime_data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "panel": {
            "connected": panel.connected,
            "info": asdict(panel.info),
            "part_arms": panel.part_arms,
            "areas": [asdict(a) for a in panel.areas.values()],
            "zones": [asdict(z) for z in panel.zones.values()],
            **panel.diagnostics(),
        },
    }
