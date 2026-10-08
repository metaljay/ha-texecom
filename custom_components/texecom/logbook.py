"""Activity-list (logbook) lines for the panel's events, so they show next to
the alarm's state changes, on the Alarm dashboard and in the logbook: which
zone set the alarm off, failed arms, tampers, faults and keypad users."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.components.logbook import LOGBOOK_ENTRY_ENTITY_ID, LOGBOOK_ENTRY_MESSAGE, LOGBOOK_ENTRY_NAME
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, EVENT
from .users import user_name


@callback
def async_describe_events(
    hass: HomeAssistant,
    async_describe_event: Callable[[str, str, Callable[[Event], dict[str, Any]]], None],
) -> None:
    @callback
    def describe(event: Event) -> dict[str, Any]:
        data = event.data
        entry = hass.config_entries.async_get_entry(data.get("entry_id", ""))
        options = entry.options if entry else {}
        entity_id = _alarm_entity(hass, entry, _area_of(entry, data))
        state = hass.states.get(entity_id) if entity_id else None
        return {
            LOGBOOK_ENTRY_NAME: state.name if state else "Texecom alarm",
            LOGBOOK_ENTRY_MESSAGE: _message(data, options),
            LOGBOOK_ENTRY_ENTITY_ID: entity_id,
        }

    async_describe_event(DOMAIN, EVENT, describe)


def _message(data: dict[str, Any], options: Any) -> str:
    kind = data.get("type")
    zone = data.get("zone_name") or f"zone {data.get('zone')}"
    if kind == "zone_alarm":
        return f"was set off by a tamper on {zone}" if data.get("tamper") else f"was set off by {zone}"
    if kind == "arm_failed":
        return f"didn't arm: {zone} was active when the exit time ended"
    if kind == "tamper":
        return f"reported a tamper: {data.get('source')}"
    if kind == "tamper_cleared":
        return f"tamper put right: {data.get('source')}"
    if kind == "fault":
        return f"reported a fault: {data.get('source')}"
    if kind == "fault_cleared":
        return f"fault cleared: {data.get('source')}"
    if kind == "user":
        how = {"code": "a code", "tag": "a tag", "code+tag": "a code and tag"}.get(data.get("method"), "the keypad")
        return f"keypad used by {user_name(options, data.get('user'))} ({how})"
    return f"reported {kind}"


def _area_of(entry: Any, data: dict[str, Any]) -> int | None:
    """The area an event belongs to, where it says: a zone's area, or the
    first area of an arm_failed event's area bitmap."""
    if data.get("type") == "arm_failed" and (areas := data.get("areas")):
        return (areas & -areas).bit_length()
    panel = getattr(entry, "runtime_data", None)
    zone = getattr(panel, "zones", {}).get(data.get("zone"))
    return zone.areas[0] if zone and zone.areas else None


def _alarm_entity(hass: HomeAssistant, entry: Any, area: int | None) -> str | None:
    """The alarm entity for that area, else the panel's first alarm."""
    if entry is None:
        return None
    registry = er.async_get(hass)
    alarms = sorted(
        (e for e in er.async_entries_for_config_entry(registry, entry.entry_id) if e.domain == "alarm_control_panel"),
        key=lambda e: e.unique_id,
    )
    for e in alarms:
        if area is not None and e.unique_id == f"{entry.entry_id}_area_{area}":
            return e.entity_id
    return alarms[0].entity_id if alarms else None
