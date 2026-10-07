"""The "Alarm not set" notification: the panel didn't arm because zones were
active when the exit time ended. It names the zones, and goes once the alarm
is armed."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .panel import TexecomPanel

ARM_FAILED_WINDOW = 60.0  # zones reported within this of each other are one failed arm


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
