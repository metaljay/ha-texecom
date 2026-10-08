"""Notifications in Home Assistant:

- "Alarm not set": the panel didn't arm because zones were active when the
  exit time ended. It names the zones, and goes once the alarm is armed.
- "Alarm panel on battery" while the panel has no mains power, and "Alarm
  tamper" while a tamper is open (each can be turned off in Options). They
  follow the Mains power and Tamper sensors."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import CONF_NOTIFY_MAINS, CONF_NOTIFY_TAMPER, DOMAIN
from .panel import MAINS_FAULTS, TexecomPanel

ARM_FAILED_WINDOW = 60.0  # zones reported within this of each other are one failed arm

MAINS_MESSAGE = (
    "The alarm panel has lost its mains power and is running on its battery. The alarm still works "
    "for now. Check the panel's power supply (its fused spur), or whether there's a power cut. "
    "The panel's *Battery voltage* shows how the battery is doing. This notice goes when the mains is back."
)

# What each tamper means, for the notice ("Panel Box Tamper" -> where to look).
TAMPER_PLACES = {
    "Panel Box Tamper": "the panel's lid is open",
    "Auxiliary Tamper": "a detector's cover is open (the panel can't tell which one)",
    "Bell Tamper": "the outside bell box",
    "Keypad Tamper": "a keypad",
    "Expander Tamper": "an expander",
    "PSU Tamper": "a power supply's box",
    "Code Tamper Alarm": "too many wrong codes were entered at a keypad",
    "Internal Alarm": (
        "the panel set off its internal sounders without saying why. The keypad shows what it is "
        "(often a detector's cover or the panel's lid), and entering a code there silences it"
    ),
}


def arm_failed_notifier(hass: HomeAssistant, entry: ConfigEntry) -> Callable[[dict[str, Any]], None]:
    """Shows or updates the notification for each arm_failed event."""
    failed_zones: list[str] = []
    failed_at = [0.0]

    @callback
    def arm_failed(details: dict[str, Any]) -> None:
        # The panel logs one entry per zone that stopped the arm; gather
        # them into one notification.
        now = time.monotonic()
        if now - failed_at[0] > ARM_FAILED_WINDOW:
            failed_zones.clear()
        failed_at[0] = now
        name = details.get("zone_name") or f"zone {details.get('zone')}"
        if name not in failed_zones:
            failed_zones.append(name)
        persistent_notification.async_create(
            hass,
            f"The panel didn't arm: **{', '.join(failed_zones)}** "
            f"{'was' if len(failed_zones) == 1 else 'were'} active when the exit time ended "
            '(the panel sounds its "fail to set" warning). Close the door or keep out of the '
            "sensor's view, then arm again.",
            title="Alarm not set",
            notification_id=f"{DOMAIN}_arm_failed_{entry.entry_id}",
        )

    return arm_failed


def dismiss_when_armed(hass: HomeAssistant, entry: ConfigEntry, panel: TexecomPanel) -> Callable[[], None]:
    """Removes the notification once an area is armed. Returns the function
    that stops watching."""

    @callback
    def armed_now() -> None:
        if any(area.state.startswith("armed") for area in panel.areas.values()):
            persistent_notification.async_dismiss(hass, f"{DOMAIN}_arm_failed_{entry.entry_id}")

    return panel.add_listener(armed_now)


def watch_conditions(hass: HomeAssistant, entry: ConfigEntry, panel: TexecomPanel) -> Callable[[], None]:
    """Shows "Alarm panel on battery" and "Alarm tamper" while those last.
    Returns the function that stops watching (and removes them)."""
    shown: dict[str, str] = {}  # kind -> the message showing

    def show(kind: str, title: str, message: str | None) -> None:
        notification_id = f"{DOMAIN}_{kind}_{entry.entry_id}"
        if message is None:
            if shown.pop(kind, None) is not None:
                persistent_notification.async_dismiss(hass, notification_id)
        elif shown.get(kind) != message:
            shown[kind] = message
            persistent_notification.async_create(hass, message, title=title, notification_id=notification_id)

    @callback
    def check() -> None:
        faults = panel.extra.get("faults", set())
        # Only once the power has been read since connecting, so a reconnect
        # doesn't remove the notice and bring it back half a minute later.
        if "power" in panel.extra or faults & MAINS_FAULTS:
            on_battery = bool(faults & MAINS_FAULTS) and entry.options.get(CONF_NOTIFY_MAINS, True)
            show("mains", "Alarm panel on battery", MAINS_MESSAGE if on_battery else None)
        tampers = sorted(panel.extra.get("tampers", ())) if entry.options.get(CONF_NOTIFY_TAMPER, True) else []
        show("tamper", "Alarm tamper", _tamper_message(tampers) if tampers else None)

    remove_listener = panel.add_listener(check)

    def remove() -> None:
        remove_listener()
        for kind in list(shown):
            show(kind, "", None)

    return remove


def _tamper_message(tampers: list[str]) -> str:
    where = "; ".join(
        f"**{name}**: {TAMPER_PLACES[name]}" if name in TAMPER_PLACES else f"**{name}**" for name in tampers
    )
    return f"The alarm panel reports a tamper. {where}. This notice goes when it's put right."
