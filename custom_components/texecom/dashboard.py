"""Builds a ready-made "Alarm" dashboard from Home Assistant's own cards.

Home Assistant has no public API for an integration to add a dashboard, so
this reaches the dashboards collection through the websocket command the
frontend uses. If that ever changes, creating the dashboard fails with a
message pointing to the README's copy-and-paste version; nothing else is
affected. So any error from Home Assistant's dashboards is caught here,
whatever its type: none may stop the alarm loading or upgrading.
"""

from __future__ import annotations

import functools
import inspect
import json
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .const import CONF_PROTOCOL, CONF_UDL, PART_ARM_OPTIONS, PROTOCOL_CONNECT

_LOGGER = logging.getLogger(__name__)

GROUPS = (
    ("Doors and windows", "mdi:door", {"door", "window", "garage_door", "opening"}),
    ("Motion", "mdi:motion-sensor", {"motion", "occupancy", "presence"}),
    ("Fire and safety", "mdi:fire", {"smoke", "gas", "carbon_monoxide", "safety", "heat"}),
    ("Other zones", "mdi:shield-outline", None),
)
# The alarm's buttons, in order. Home Assistant shows only the modes the alarm
# offers, so the dashboard follows Configure → Arm buttons without a rebuild.
MODES = ["armed_away", "armed_night", "armed_home", "armed_vacation", "armed_custom_bypass", "disarmed"]
# The labelled buttons on the alarm's card, in order. That card shows every
# button it's given, so it lists only the ones the alarm offers, and is kept
# in step with Configure → Arm buttons each time the panel is set up.
ARM_BUTTONS = {
    "away": "arm_away",
    "night": "arm_night",
    "home": "arm_home",
    "vacation": "arm_vacation",
    "custom_bypass": "arm_custom_bypass",
}
URL_PATH = "texecom-alarm"
NOT_CONNECTED = (
    "**Home Assistant can't reach the alarm panel.** It keeps trying and reconnects by itself. "
    "Your alarm still works as normal at the keypad."
)


def _tile(entity_id: str, **extra: Any) -> dict[str, Any]:
    return {"type": "tile", "entity": entity_id, **extra}


def arm_buttons(entry: ConfigEntry) -> list[str]:
    """The arm buttons this panel's alarms offer: Away always, the others when
    a part arm is mapped; none without the UDL code over Crestron."""
    if entry.data.get(CONF_PROTOCOL) != PROTOCOL_CONNECT and not entry.data.get(CONF_UDL):
        return []
    return [ARM_BUTTONS["away"]] + [ARM_BUTTONS[m] for m, key in PART_ARM_OPTIONS.items() if entry.options.get(key)]


def build_config(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Four sections, in the order a phone shows them: the alarm, the zones,
    recent activity, then the panel's health."""
    registry = er.async_get(hass)
    entities = [e for e in er.async_entries_for_config_entry(registry, entry.entry_id) if not e.disabled_by]
    by_key = {e.unique_id: e.entity_id for e in entities}
    buttons = arm_buttons(entry)

    alarm_cards: list[dict[str, Any]] = [{"type": "heading", "heading": "Alarm", "icon": "mdi:shield-home"}]
    if connection := by_key.get(f"{entry.entry_id}_connection"):
        alarm_cards.append(
            {
                "type": "conditional",
                "conditions": [{"condition": "state", "entity": connection, "state": "off"}],
                "card": {"type": "markdown", "content": NOT_CONNECTED},
                "grid_options": {"columns": "full"},
            }
        )
    alarms: list[str] = []
    for e in entities:
        if e.domain != "alarm_control_panel":
            continue
        alarms.append(e.entity_id)
        if buttons:
            alarm_cards.append(
                {
                    "type": "alarm-panel",
                    "entity": e.entity_id,
                    "states": list(buttons),
                    "grid_options": {"columns": "full"},
                }
            )
        else:  # state only: nothing to press
            alarm_cards.append(_tile(e.entity_id, grid_options={"columns": "full"}))
        if ready := by_key.get(f"{e.unique_id}_ready"):
            alarm_cards.append(_tile(ready, name="Ready to arm", color="green", grid_options={"columns": "full"}))

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

    activity_cards: list[dict[str, Any]] = [
        {"type": "heading", "heading": "Activity", "icon": "mdi:history"},
        {
            "type": "logbook",
            "target": {"entity_id": alarms},
            "hours_to_show": 24,
            "grid_options": {"columns": "full", "rows": 4},
        },
    ]

    panel_cards: list[dict[str, Any]] = [{"type": "heading", "heading": "Panel", "icon": "mdi:information-outline"}]
    for key, extra in (
        ("connection", {"name": "Connection", "color": "green"}),
        ("mains", {"name": "Mains power", "color": "green"}),
        ("problem", {"name": "Faults"}),
        ("system_tamper", {"name": "Tamper"}),
        ("battery_voltage", {"name": "Battery"}),
        ("display", {"name": "Keypad shows"}),
    ):
        if entity_id := by_key.get(f"{entry.entry_id}_{key}"):
            panel_cards.append(_tile(entity_id, **extra))

    sections = [alarm_cards, zone_cards, activity_cards, panel_cards]
    return {
        "title": "Alarm",
        "views": [
            {
                "title": "Alarm",
                "path": "alarm",
                "type": "sections",
                "max_columns": 3,
                "sections": [{"type": "grid", "cards": cards} for cards in sections if cards],
            }
        ],
    }


def _build_config_v1(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """The dashboard as made up to 0.2.16, kept only to tell whether a saved
    one was ever edited (async_upgrade_layout)."""
    registry = er.async_get(hass)
    entities = [e for e in er.async_entries_for_config_entry(registry, entry.entry_id) if not e.disabled_by]

    alarm_cards: list[dict[str, Any]] = [{"type": "heading", "heading": "Alarm", "icon": "mdi:shield-home"}]
    alarms: list[str] = []
    for e in entities:
        if e.domain != "alarm_control_panel":
            continue
        alarms.append(e.entity_id)
        alarm_cards.append(
            _tile(
                e.entity_id,
                vertical=False,
                features=[{"type": "alarm-modes", "modes": MODES}],
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
    except Exception as err:  # Home Assistant's internals: anything may have changed (see above)
        _LOGGER.warning("Couldn't add the Alarm dashboard (%r); the README has a copy-and-paste version", err)
        raise HomeAssistantError("couldn't add the dashboard") from err
    _LOGGER.info("Added the Alarm dashboard at /%s", url_path)
    return url_path


async def async_update_alarm_buttons(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Dashboards made before 0.2.12 listed only the modes the alarm had then:
    lists every mode on this panel's alarm cards so they follow Configure →
    Arm buttons, keeping everything else the user changed. Lists the user
    reordered or added to are left alone. If Home Assistant's dashboards
    won't have it, the upgrade carries on without."""
    try:
        await _update_alarm_buttons(hass, entry, _list_every_mode)
    except Exception as err:  # Home Assistant's internals: anything may have changed (see above)
        _LOGGER.warning("Couldn't update the Alarm dashboard's buttons (%r); refreshing it in Configure will", err)


async def async_sync_arm_buttons(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Keeps the labelled buttons on this panel's alarm cards in step with
    Configure → Arm buttons (run at each setup, which follows every change
    there). Lists the user reordered or added to are left alone, and nothing
    is saved when they already match."""
    try:
        await _update_alarm_buttons(hass, entry, functools.partial(_set_arm_buttons, buttons=arm_buttons(entry)))
    except Exception as err:  # Home Assistant's internals: anything may have changed (see above)
        _LOGGER.warning("Couldn't update the Alarm dashboard's buttons (%r); refreshing it in Configure will", err)


async def async_upgrade_layout(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Gives a dashboard nobody edited the current layout (0.2.17: labelled
    arm buttons, zones before activity). One that was edited, or made by a
    release whose layout differed, is left as it is: Configure → Rebuild the
    Alarm dashboard replaces it when the user chooses."""
    try:
        dashboards = hass.data["lovelace"].dashboards
        before = _build_config_v1(hass, entry)
        for url_path in (f"{URL_PATH}-{entry.entry_id[-6:].lower()}", URL_PATH):
            if (dashboard := dashboards.get(url_path)) is None:
                continue
            try:
                config = await dashboard.async_load(False)
            except HomeAssistantError:  # never saved
                continue
            if config == before:
                await dashboard.async_save(build_config(hass, entry))
                _LOGGER.info("The Alarm dashboard has its new layout (/%s)", url_path)
    except Exception as err:  # Home Assistant's internals: anything may have changed (see above)
        _LOGGER.warning("Couldn't give the Alarm dashboard its new layout (%r); rebuilding it in Configure will", err)


async def _update_alarm_buttons(hass: HomeAssistant, entry: ConfigEntry, change: Any) -> None:
    """Applies `change(config, alarms)` to each of this panel's dashboards,
    saving the ones it says it changed."""
    try:
        dashboards = hass.data["lovelace"].dashboards
    except (KeyError, AttributeError):
        return
    registry = er.async_get(hass)
    alarms = {
        e.entity_id
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.domain == "alarm_control_panel"
    }
    for url_path in (f"{URL_PATH}-{entry.entry_id[-6:].lower()}", URL_PATH):
        if (dashboard := dashboards.get(url_path)) is None:
            continue
        try:
            config = await dashboard.async_load(False)
        except HomeAssistantError:  # never saved
            continue
        if change(config, alarms):
            await dashboard.async_save(config)
            _LOGGER.info("The Alarm dashboard's buttons now follow the arm buttons (/%s)", url_path)


def _set_arm_buttons(node: Any, alarms: set[str], *, buttons: list[str]) -> bool:
    """Sets the buttons on the alarm-panel cards showing one of `alarms`,
    anywhere in a dashboard; True if anything changed."""
    changed = False
    if isinstance(node, dict):
        if node.get("type") == "alarm-panel" and node.get("entity") in alarms:
            current = node.get("states") or []
            # Only lists as this integration made them: some buttons, in order.
            if current != buttons and current == [b for b in ARM_BUTTONS.values() if b in current]:
                node["states"] = list(buttons)
                changed = True
        for value in node.values():
            changed |= _set_arm_buttons(value, alarms, buttons=buttons)
    elif isinstance(node, list):
        for item in node:
            changed |= _set_arm_buttons(item, alarms, buttons=buttons)
    return changed


def _list_every_mode(node: Any, alarms: set[str]) -> bool:
    """Lists every mode on the alarm-modes feature of tiles showing one of
    `alarms`, anywhere in a dashboard; True if anything changed."""
    changed = False
    if isinstance(node, dict):
        if node.get("type") == "tile" and node.get("entity") in alarms:
            for feature in node.get("features") or []:
                if not isinstance(feature, dict) or feature.get("type") != "alarm-modes":
                    continue
                modes = feature.get("modes") or []
                # Only lists as this integration made them: some modes, in order.
                if modes != MODES and modes == [m for m in MODES if m in modes]:
                    feature["modes"] = list(MODES)
                    changed = True
        for value in node.values():
            changed |= _list_every_mode(value, alarms)
    elif isinstance(node, list):
        for item in node:
            changed |= _list_every_mode(item, alarms)
    return changed


async def async_remove_dashboard(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Deletes this panel's dashboard when the panel is deleted, so no "Alarm"
    is left in the sidebar with every entity missing."""
    try:
        handler = hass.data["websocket_api"]["lovelace/dashboards/create"][0]
        collection = inspect.unwrap(handler).__self__.storage_collection
        dashboards = hass.data["lovelace"].dashboards
    except (KeyError, AttributeError, TypeError, IndexError):
        return  # no dashboards to delete
    own = f"{URL_PATH}-{entry.entry_id[-6:].lower()}"
    for url_path in (own, URL_PATH):
        if url_path not in dashboards:
            continue
        if url_path == own or await _shows_entry(hass, entry, dashboards[url_path]):
            for item in list(collection.async_items()):
                if item.get("url_path") == url_path:
                    await collection.async_delete_item(item["id"])
                    _LOGGER.info("Deleted the Alarm dashboard at /%s", url_path)
