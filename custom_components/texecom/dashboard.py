"""Builds a ready-made "Alarm" dashboard from Home Assistant's own cards.

Home Assistant has no public API for an integration to add a dashboard, so
this reaches the dashboards collection through the websocket command the
frontend uses. If that ever changes, creating the dashboard fails with a
message pointing to the README's copy-and-paste version; nothing else is
affected.
"""

from __future__ import annotations

import inspect
import json
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

_LOGGER = logging.getLogger(__name__)

GROUPS = (
    ("Doors and windows", "mdi:door", {"door", "window", "garage_door", "opening"}),
    ("Motion", "mdi:motion-sensor", {"motion", "occupancy", "presence"}),
    ("Fire and safety", "mdi:fire", {"smoke", "gas", "carbon_monoxide", "safety", "heat"}),
    ("Other zones", "mdi:shield-outline", None),
)
MODES = {2: "armed_away", 4: "armed_night", 1: "armed_home"}  # feature bit -> mode
URL_PATH = "texecom-alarm"


def _tile(entity_id: str, **extra: Any) -> dict[str, Any]:
    return {"type": "tile", "entity": entity_id, **extra}


def build_config(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    registry = er.async_get(hass)
    entities = [e for e in er.async_entries_for_config_entry(registry, entry.entry_id) if not e.disabled_by]

    alarm_cards: list[dict[str, Any]] = [{"type": "heading", "heading": "Alarm", "icon": "mdi:shield-home"}]
    alarms: list[str] = []
    for e in entities:
        if e.domain != "alarm_control_panel":
            continue
        alarms.append(e.entity_id)
        features = (e.supported_features or 0) if e.supported_features is not None else 7
        modes = [mode for bit, mode in MODES.items() if features & bit] + ["disarmed"]
        alarm_cards.append(
            _tile(
                e.entity_id,
                vertical=False,
                features=[{"type": "alarm-modes", "modes": modes}],
                features_position="bottom",
                grid_options={"columns": "full"},
            )
        )
        for ready in entities:
            if ready.unique_id == f"{e.unique_id}_ready":
                alarm_cards.append(_tile(ready.entity_id, name="Ready to arm", grid_options={"columns": "full"}))
    alarm_cards.append(
        {
            "type": "logbook",
            "target": {"entity_id": alarms},
            "hours_to_show": 24,
            "grid_options": {"columns": "full", "rows": 4},
        }
    )

    zones = [
        e
        for e in entities
        if e.domain == "binary_sensor"
        and e.unique_id.split("_", 1)[1].startswith("zone_")
        and not e.unique_id.endswith("_tamper")
    ]
    zone_cards: list[dict[str, Any]] = []
    used: set[str] = set()
    for heading, icon, classes in GROUPS:
        group = [
            e
            for e in zones
            if e.entity_id not in used
            and (classes is None or (e.device_class or e.original_device_class or "") in classes)
        ]
        if not group:
            continue
        zone_cards.append({"type": "heading", "heading": heading, "icon": icon})
        for e in group:
            used.add(e.entity_id)
            zone_cards.append(_tile(e.entity_id))

    panel_cards: list[dict[str, Any]] = [{"type": "heading", "heading": "Panel", "icon": "mdi:information-outline"}]
    for key, name in (
        ("connection", "Connection"),
        ("mains", "Mains"),
        ("problem", "Faults"),
        ("system_tamper", "Tamper"),
        ("display", "Keypad"),
        ("battery_voltage", "Battery"),
    ):
        for e in entities:
            if e.unique_id == f"{entry.entry_id}_{key}":
                panel_cards.append(_tile(e.entity_id, name=name))

    return {
        "title": "Alarm",
        "views": [
            {
                "title": "Alarm",
                "path": "alarm",
                "type": "sections",
                "max_columns": 3,
                "sections": [
                    {"type": "grid", "cards": alarm_cards + panel_cards},
                    {"type": "grid", "cards": zone_cards},
                ],
            }
        ],
    }


async def _url_path(hass: HomeAssistant, entry: ConfigEntry, dashboards: dict[str, Any]) -> str:
    """This panel's dashboard. The first panel's is "texecom-alarm"; with
    more than one panel, the others' end with part of their entry ID. It's
    looked for rather than worked out again: a panel added since doesn't
    change which one the first panel has."""
    own = f"{URL_PATH}-{entry.entry_id[-6:].lower()}"
    if own in dashboards:
        return own
    others = [e for e in hass.config_entries.async_entries(entry.domain) if e.entry_id != entry.entry_id]
    if not others or (URL_PATH in dashboards and await _shows_entry(hass, entry, dashboards[URL_PATH])):
        return URL_PATH
    return own


async def _shows_entry(hass: HomeAssistant, entry: ConfigEntry, dashboard: Any) -> bool:
    """Whether a saved dashboard shows any of this panel's entities."""
    try:
        text = json.dumps(await dashboard.async_load(False))
    except HomeAssistantError:  # never saved
        return False
    registry = er.async_get(hass)
    return any(f'"{e.entity_id}"' in text for e in er.async_entries_for_config_entry(registry, entry.entry_id))


async def async_create_dashboard(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Adds (or refreshes) the Alarm dashboard; returns its URL path."""
    try:
        handler = hass.data["websocket_api"]["lovelace/dashboards/create"][0]
        collection = inspect.unwrap(handler).__self__.storage_collection
        lovelace = hass.data["lovelace"]
        url_path = await _url_path(hass, entry, lovelace.dashboards)
        if url_path not in lovelace.dashboards:
            await collection.async_create_item(
                {
                    "url_path": url_path,
                    "title": "Alarm",
                    "icon": "mdi:shield-home",
                    "show_in_sidebar": True,
                    "require_admin": False,
                }
            )
        await lovelace.dashboards[url_path].async_save(build_config(hass, entry))
    except (KeyError, AttributeError, TypeError, IndexError) as err:
        _LOGGER.warning("Couldn't add the Alarm dashboard (%r); the README has a copy-and-paste version", err)
        raise HomeAssistantError("couldn't add the dashboard") from err
    _LOGGER.info("Added the Alarm dashboard at /%s", url_path)
    return url_path
