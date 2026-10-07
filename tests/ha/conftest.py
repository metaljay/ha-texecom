"""Fixtures for the Home Assistant tests: a fake Connect panel on localhost,
short timings, and helpers to set up the integration."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fake_connect_panel import DEMO_ZONES, FakeConnectPanel  # noqa: E402

from custom_components.texecom.connect import client as connect_client  # noqa: E402
from custom_components.texecom.connect import discovery as connect_discovery  # noqa: E402
from custom_components.texecom.connect import panel as connect_panel  # noqa: E402


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def fast_timings(monkeypatch):
    monkeypatch.setattr(connect_client, "LOGIN_DELAY", 0)
    monkeypatch.setattr(connect_client, "COMMAND_TIMEOUT", 0.5)
    monkeypatch.setattr(connect_client, "COMMAND_ATTEMPTS", 2)
    monkeypatch.setattr(connect_client, "KEEPALIVE", 3600)
    monkeypatch.setattr(connect_panel, "RECONNECT_MIN", 0.05)
    monkeypatch.setattr(connect_panel, "READY_CHECK_DELAY", 0.05)
    monkeypatch.setattr(connect_discovery, "PROBE_PATIENCE", 0)


@pytest.fixture
async def fake(socket_enabled):
    panel = FakeConnectPanel(zones=DEMO_ZONES, exit_delay=0.05)
    await panel.start()
    yield panel
    await panel.close()


@pytest.fixture
def no_setup():
    """Flow tests stop at the entry: don't connect."""
    with patch("custom_components.texecom.async_setup_entry", return_value=True) as mock:
        yield mock
