"""Notifications in Home Assistant:

- "Alarm not set": the panel didn't arm because zones were active when the
  exit time ended. It names the zones, and goes once that area is armed.
- "Alarm panel on battery" once the panel has had no mains power for two
  minutes (so a short blip shows nothing), and "Alarm tamper" while a tamper
  is open, the panel's own or a zone's (each can be turned off in Options).
  They follow the Mains power and Tamper sensors."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

from .const import CONF_NOTIFY_MAINS, CONF_NOTIFY_TAMPER, DOMAIN, EVENT
from .panel import MAINS_FAULTS, TexecomPanel, nice_name

ARM_FAILED_WINDOW = 60.0  # zones reported within this of each other are one failed arm
MAINS_NOTICE_AFTER = 120.0  # seconds on battery before the notice: power blips come and go sooner

MAINS_MESSAGE = (
    "The panel has lost its mains power and is running on its battery, so the alarm still works for now. "
    "Check for a power cut, or the panel's power supply (its fused spur). This notice goes when the mains is back."
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
ZONE_TAMPER_PLACE = "its detector's cover is open, or its wiring is damaged"


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
            f"**{', '.join(failed_zones)}** {'was' if len(failed_zones) == 1 else 'were'} still active when "
            "the exit time ended, so the alarm didn't arm. Close the door or keep out of the sensor's view, "
            "then arm again.",
            title="Alarm not set",
            notification_id=f"{DOMAIN}_arm_failed_{entry.entry_id}",
        )

    return arm_failed


def dismiss_when_armed(hass: HomeAssistant, entry: ConfigEntry, panel: TexecomPanel) -> Callable[[], None]:
    """Removes the notification once an area that didn't arm is armed: the
    areas the panel named in its arm_failed events, or any area if it named
    none. An area that was armed already (a garage that's always armed, say)
    doesn't remove it. Returns the function that stops watching."""
    failed: set[int] = set()  # the areas that didn't arm
    failed_at = [0.0]
    armed: set[int] = set()  # the areas armed at the last check

    @callback
    def arm_failed(event: Event) -> None:
        if event.data.get("type") != "arm_failed" or event.data.get("entry_id") != entry.entry_id:
            return
        now = time.monotonic()
        if now - failed_at[0] > ARM_FAILED_WINDOW:  # a new notification, as arm_failed_notifier makes
            failed.clear()
        failed_at[0] = now
        bitmap = event.data.get("areas") or 0
        failed.update(number for number in panel.areas if bitmap & (1 << (number - 1)))

    @callback
    def armed_now() -> None:
        now_armed = {number for number, area in panel.areas.items() if area.state.startswith("armed")}
        newly = now_armed - armed
        armed.clear()
        armed.update(now_armed)
        if newly and (newly & failed or not failed):
            failed.clear()
            persistent_notification.async_dismiss(hass, f"{DOMAIN}_arm_failed_{entry.entry_id}")

    remove_event = hass.bus.async_listen(EVENT, arm_failed)
    remove_listener = panel.add_listener(armed_now)

    def remove() -> None:
        remove_event()
        remove_listener()

    return remove


def watch_conditions(hass: HomeAssistant, entry: ConfigEntry, panel: TexecomPanel) -> Callable[[], None]:
    """Shows "Alarm panel on battery" and "Alarm tamper" while those last.
    Returns the function that stops watching (and removes them)."""
    shown: dict[str, str] = {}  # kind -> the message showing
    mains_timer: CALLBACK_TYPE | None = None  # waiting to show the battery notice

    def show(kind: str, title: str, message: str | None) -> None:
        notification_id = f"{DOMAIN}_{kind}_{entry.entry_id}"
        if message is None:
            if shown.pop(kind, None) is not None:
                persistent_notification.async_dismiss(hass, notification_id)
        elif shown.get(kind) != message:
            shown[kind] = message
            persistent_notification.async_create(hass, message, title=title, notification_id=notification_id)

    @callback
    def still_on_battery(_now=None) -> None:
        nonlocal mains_timer
        mains_timer = None
        show("mains", "Alarm panel on battery", MAINS_MESSAGE)

    def cancel_mains_timer() -> None:
        nonlocal mains_timer
        if mains_timer:
            mains_timer()
            mains_timer = None

    @callback
    def check() -> None:
        nonlocal mains_timer
        faults = panel.extra.get("faults", set())
        # Only once the power has been read since connecting, so a reconnect
        # doesn't remove the notice and bring it back half a minute later.
        if "power" in panel.extra or faults & MAINS_FAULTS:
            on_battery = bool(faults & MAINS_FAULTS) and entry.options.get(CONF_NOTIFY_MAINS, True)
            if not on_battery:
                cancel_mains_timer()
                show("mains", "", None)
            elif "mains" not in shown and mains_timer is None:
                mains_timer = async_call_later(hass, MAINS_NOTICE_AFTER, still_on_battery)
        tampers: list[str] = []
        if entry.options.get(CONF_NOTIFY_TAMPER, True):
            tampers = [
                f"**{name}**: {TAMPER_PLACES[name]}" if name in TAMPER_PLACES else f"**{name}**"
                for name in sorted(panel.extra.get("tampers", ()))
            ]
            tampers += [
                f"**{nice_name(zone.name)}**: {ZONE_TAMPER_PLACE}"
                for _number, zone in sorted(panel.zones.items())
                if zone.tampered
            ]
        show("tamper", "Alarm tamper", _tamper_message(tampers) if tampers else None)

    remove_listener = panel.add_listener(check)

    def remove() -> None:
        remove_listener()
        cancel_mains_timer()
        for kind in list(shown):
            show(kind, "", None)

    return remove


def _tamper_message(tampers: list[str]) -> str:
    return f"The alarm panel reports a tamper. {'; '.join(tampers)}. This notice goes when it's put right."
