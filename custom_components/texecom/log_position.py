"""Where the panel's stored event log was last read up to, kept across reloads
and restarts.

Without it, the first reading after a reload or restart only notes where the
log ends, so whatever the panel logged in between is never read: on a real
Elite 168 (3 Nov 2026), a receiver dropping out and the tamper alarm it caused
were logged while the readings were held up, and a reload three hours later
skipped them. With it, the driver carries on from where it was (up to its
catch-up limit). Kept in Home Assistant's storage, one small file per panel,
not in the config entry: it changes with every reading."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .panel import TexecomPanel

STORE_VERSION = 1
SAVE_DELAY = 10  # seconds: readings come in bursts


def _store(hass: HomeAssistant, entry_id: str) -> Store[dict[str, int]]:
    # One Store per panel for as long as Home Assistant runs, so a reload
    # loads what a save still waiting to be written holds.
    stores: dict[str, Store[dict[str, int]]] = hass.data.setdefault(f"{DOMAIN}_log_position", {})
    if entry_id not in stores:
        stores[entry_id] = Store(hass, STORE_VERSION, f"{DOMAIN}.{entry_id}.log_position")
    return stores[entry_id]


async def resume_log_position(hass: HomeAssistant, entry: ConfigEntry, panel: TexecomPanel) -> None:
    """Before the panel starts: hand it where the last run stopped reading."""
    saved = await _store(hass, entry.entry_id).async_load()
    if isinstance(saved, dict) and isinstance(saved.get("pointer"), int):
        panel.resume_log_from(saved["pointer"])


def keep_log_position(hass: HomeAssistant, entry: ConfigEntry, panel: TexecomPanel) -> Callable[[], None]:
    """Saves the position whenever it moves; returns the function that stops it."""
    store = _store(hass, entry.entry_id)
    saved = panel.log_pointer

    @callback
    def changed() -> None:
        nonlocal saved
        pointer = panel.log_pointer
        if pointer is not None and pointer != saved:
            saved = pointer
            store.async_delay_save(lambda: {"pointer": pointer}, SAVE_DELAY)

    stop_listening = panel.add_listener(changed)

    def stop() -> None:
        stop_listening()
        changed()  # the panel has stopped by now: its last position

    return stop


async def remove_log_position(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await _store(hass, entry.entry_id).async_remove()
    hass.data.get(f"{DOMAIN}_log_position", {}).pop(entry.entry_id, None)
