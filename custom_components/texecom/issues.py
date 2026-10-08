"""Repairs notices: the panel clock is wrong (fixable, see repairs.py), and
the panel has been unreachable for a while."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval

from .const import CLOCK_DRIFT_LIMIT, CONF_SERIAL_DEVICE, DOMAIN, HELP_OFFLINE
from .panel import TexecomPanel

OFFLINE_ISSUE_AFTER = 15 * 60  # seconds without a connection before Repairs says so


def describe_drift(drift: int | None) -> str:
    """E.g. "about 3 years behind", "12 minutes ahead"; None: an impossible date."""
    if drift is None:
        return "not set (it holds an impossible date)"
    seconds = abs(drift)
    for unit, size in (("year", 365 * 86400), ("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            count = round(seconds / size)
            amount = f"{count} {unit}{'s' if count != 1 else ''}"
            break
    else:
        amount = f"{seconds} seconds"
    return f"about {amount} {'ahead' if drift > 0 else 'behind'}"


def clock_drift_reporter(hass: HomeAssistant, entry: ConfigEntry) -> Callable[[int | None], None]:
    """The Connect panel's on_clock_drift (used while clock sync is off): a
    notice while the clock is more than CLOCK_DRIFT_LIMIT out, cleared once
    it's right."""

    @callback
    def clock_drift(drift: int | None) -> None:
        issue_id = f"panel_clock_{entry.entry_id}"
        if drift is not None and abs(drift) <= CLOCK_DRIFT_LIMIT:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
            return
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="panel_clock_wrong",
            translation_placeholders={"drift": describe_drift(drift)},
            data={"entry_id": entry.entry_id},
        )

    return clock_drift


def clear_clock_issue(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clock sync is on: it sets the clock, so no notice is needed."""
    ir.async_delete_issue(hass, DOMAIN, f"panel_clock_{entry.entry_id}")


def watch_connection(hass: HomeAssistant, entry: ConfigEntry, panel: TexecomPanel):
    """Raises a Repairs notice when the panel has been unreachable for a while
    (e.g. the SmartCom's address changed), and clears it once connected."""
    issue_id = f"panel_offline_{entry.entry_id}"
    address = entry.data.get(CONF_SERIAL_DEVICE) or f"{entry.data.get(CONF_HOST)}:{entry.data.get(CONF_PORT)}"

    @callback
    def check(_now=None) -> None:
        since = panel.disconnected_since
        if panel.connected or since is None:
            ir.async_delete_issue(hass, DOMAIN, issue_id)
            return
        minutes = (time.monotonic() - since) / 60
        if minutes * 60 < OFFLINE_ISSUE_AFTER:
            return
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="panel_offline",
            translation_placeholders={
                "name": entry.title,
                "address": address,
                "minutes": str(round(minutes)),
                "error": getattr(panel, "last_error", None) or "no reply",
                "help": HELP_OFFLINE,
            },
        )

    remove_timer = async_track_time_interval(hass, check, timedelta(minutes=1))
    remove_listener = panel.add_listener(lambda: panel.connected and check())

    def remove() -> None:
        remove_timer()
        remove_listener()
        ir.async_delete_issue(hass, DOMAIN, issue_id)

    return remove
