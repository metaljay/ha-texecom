"""Connect protocol and driver tests against the fake panel."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import _paths  # noqa: F401
import pytest
from fake_connect_panel import FakeConnectPanel

from custom_components.texecom.connect import protocol as P
from custom_components.texecom.connect.client import ConnectClient, LoginRejected, PanelBusyError, Timing
from custom_components.texecom.connect.panel import ConnectPanel, probe
from custom_components.texecom.panel import ARMED_AWAY, ARMED_HOME, ARMED_NIGHT, DISARMED, TRIGGERED, PanelError

FAST = Timing(command_timeout=0.3, command_attempts=2, keepalive=30, login_delay=0)


async def wait_for(predicate, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not predicate():
        if loop.time() > end:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


@pytest.fixture
async def fake():
    panel = FakeConnectPanel()
    await panel.start()
    yield panel
    await panel.close()


async def make_panel(fake: FakeConnectPanel, **kwargs) -> ConnectPanel:
    client = ConnectClient("127.0.0.1", fake.port, "1234", timing=FAST)
    await client.connect()
    from custom_components.texecom.connect.panel import discover

    info, zones, areas = await discover(client)
    await client.close()
    opts = {"part_arms": {"night": 1, "home": 0}, **kwargs}
    panel = ConnectPanel(
        "127.0.0.1", fake.port, "1234", info=info, zones=zones, areas=areas, timing=FAST, reconnect_min=0.05, **opts
    )
    await panel.start()
    await wait_for(lambda: panel.connected)
    return panel


# ─── Protocol ──────────────────────────────────────────────────────────────


def test_crc_and_frame_roundtrip():
    frames = []
    parser = P.FrameParser(frames.append)
    raw = P.encode_command(7, P.CMD_GET_DATE_TIME)
    parser.push(b"\x00junk" + raw[:3])
    parser.push(raw[3:])
    assert len(frames) == 1 and frames[0].sequence == 7 and frames[0].body == bytes([P.CMD_GET_DATE_TIME])


def test_bad_crc_is_dropped_and_plus_plus_plus_is_a_hangup():
    frames, drops = [], []
    parser = P.FrameParser(frames.append, drops.append)
    bad = bytearray(P.encode_command(1, 2))
    bad[-1] ^= 0xFF
    parser.push(bytes(bad))
    assert frames == []
    parser.push(b"+++")
    assert drops


def test_area_flags_decoding():
    flags = bytearray(72)
    flags[P.FLAG_ARMED] = flags[P.FLAG_PART_ARMED] = flags[P.FLAG_PART_ARM_2] = 1
    assert P.decode_area_flags(bytes(flags), [1, 2], 24) == {1: ("part armed", 2), 2: ("disarmed", None)}
    flags = bytearray(72)
    flags[P.FLAG_PART_ARMING] = 1
    assert P.decode_area_flags(bytes(flags), [1], 24)[1] == ("in exit", None)


def test_message_decoding():
    zone = P.decode_message(bytes([P.MSG_ZONE, 3, 0x21]))
    assert zone["zone"] == 3 and zone["state"].active and zone["state"].manual_bypass
    assert P.decode_message(bytes([P.MSG_AREA, 1, 4]))["state"] == "part armed"
    assert P.decode_message(bytes([P.MSG_AREA, 1, 6]))["state"] == "unknown (6)"


def test_panel_identification():
    ident = P.decode_panel_identification(b"Elite 24     V6.05.03LS1".ljust(32, b"\0"))
    assert (ident.model, ident.zones, ident.firmware) == ("Premier Elite", 24, "V6.05.03LS1")


# ─── Driver ────────────────────────────────────────────────────────────────


async def test_probe_discovers_zones_and_areas(fake):
    info, zones, areas = await probe("127.0.0.1", fake.port, "1234")
    assert info.zones == 24
    assert [z.name for z in zones] == ["Hallway", "Lounge", "Kitchen", "Garage", "Landing"]
    assert [(a.number, a.name) for a in areas] == [(1, "HOUSE")]


async def test_wrong_udl_is_rejected(fake):
    with pytest.raises(LoginRejected):
        await probe("127.0.0.1", fake.port, "9999")


async def test_zone_events_and_initial_state(fake):
    fake.zone_state[2] = 1
    panel = await make_panel(fake)
    try:
        assert panel.zones[2].active and not panel.zones[1].active
        assert panel.areas[1].state == DISARMED
        assert panel.extra["power"].panel_voltage == pytest.approx(13.77)
        fake.set_zone(1, 1)
        await wait_for(lambda: panel.zones[1].active)
        fake.set_zone(1, 2)
        await wait_for(lambda: panel.zones[1].tampered)
    finally:
        await panel.stop()


async def test_arm_night_uses_mapped_part_arm_then_disarm(fake):
    panel = await make_panel(fake)
    try:
        await panel.arm(1, "night")
        assert (P.CMD_ARM_AREA, bytes([1, 1])) in fake.commands
        await wait_for(lambda: panel.areas[1].state == ARMED_NIGHT)
        assert panel.areas[1].part_arm == 1
        assert panel.areas[1].changed_by == "Home Assistant"
        await panel.disarm(1)
        await wait_for(lambda: panel.areas[1].state == DISARMED)
    finally:
        await panel.stop()


async def test_switching_mode_disarms_first_and_unmapped_mode_is_refused(fake):
    panel = await make_panel(fake, part_arms={"night": 1, "home": 2})
    try:
        await panel.arm(1, "away")
        await wait_for(lambda: panel.areas[1].state == ARMED_AWAY)
        await panel.arm(1, "home")
        cmds = [c for c, _a in fake.commands]
        assert cmds[-2:] == [P.CMD_DISARM_AREA, P.CMD_ARM_AREA]
        await wait_for(lambda: panel.areas[1].state == ARMED_HOME)
        panel.part_arms["home"] = 0
        with pytest.raises(PanelError):
            await panel.arm(1, "home")
    finally:
        await panel.stop()


async def test_keypad_part_arm_not_mapped_shows_as_home(fake):
    panel = await make_panel(fake, part_arms={"night": 1, "home": 0})
    try:
        fake.send_user(3)
        fake.send_log(79, 0, 0)  # PART_ARM_2 log
        fake.set_area(4, 2)
        await wait_for(lambda: panel.areas[1].state == ARMED_HOME and panel.areas[1].part_arm == 2)
        assert panel.areas[1].changed_by == "User 3"
    finally:
        await panel.stop()


async def test_stale_exit_flag_is_ignored_while_armed(fake):
    panel = await make_panel(fake)
    try:
        fake.set_area(3)
        await wait_for(lambda: panel.areas[1].state == ARMED_AWAY)
        panel._apply_area(1, "in exit", None)
        assert panel.areas[1].state == ARMED_AWAY
    finally:
        await panel.stop()


async def test_disarm_in_alarm_resets_first(fake):
    panel = await make_panel(fake)
    try:
        fake.set_area(5)
        await wait_for(lambda: panel.areas[1].state == TRIGGERED)
        await panel.disarm(1)
        assert [c for c, _a in fake.commands][-2:] == [P.CMD_RESET_AREA, P.CMD_DISARM_AREA]
    finally:
        await panel.stop()


async def test_busy_nak_does_not_change_state(fake):
    panel = await make_panel(fake)
    try:
        fake.zone_state[1] = 0
        fake.nak_next[P.CMD_GET_ZONE_STATE] = 1
        with pytest.raises(PanelBusyError):
            await panel.refresh()
        assert not panel.zones[1].active  # a NAK byte (0x15) would read as "active"
    finally:
        await panel.stop()


async def test_reconnects_after_the_panel_hangs_up(fake):
    events = []
    panel = await make_panel(fake, on_event=lambda t, d: events.append((t, d)))
    try:
        fake.drop_all(alarm=True)
        await wait_for(lambda: not panel.connected)
        await wait_for(lambda: panel.connected and panel.areas[1].state == TRIGGERED)
        fake.send_log(4, 3, 2)  # zone 2 alarm
        await wait_for(lambda: events)
        assert events[0] == ("zone_alarm", {"zone": 2, "zone_name": "Lounge", "tamper": False})
    finally:
        await panel.stop()


async def test_auth_failure_stops_retrying(fake):
    calls = []
    info, zones, areas = await probe("127.0.0.1", fake.port, "1234")
    panel = ConnectPanel(
        "127.0.0.1",
        fake.port,
        "0000",
        {"night": 1},
        info,
        zones,
        areas,
        on_auth_failed=lambda: calls.append(1),
        timing=FAST,
        reconnect_min=0.05,
    )
    await panel.start()
    try:
        await wait_for(lambda: calls)
        await asyncio.sleep(0.2)
        assert calls == [1]
    finally:
        await panel.stop()


async def test_clock_sync_uses_the_configured_time_zone(fake):
    tz = ZoneInfo("Europe/London")
    panel = await make_panel(fake, time_zone=tz)
    try:
        now = datetime.now(tz)
        # The panel's clock is the fake's local time; pretend it's 10 minutes out.
        fake.clock_offset = (now.replace(tzinfo=None) - datetime.now()) + timedelta(minutes=10)
        assert await panel.sync_clock(now) is True
        assert fake.clock_set_to == P.encode_date_time(now.year, now.month, now.day, now.hour, now.minute, now.second)
        fake.clock_offset = now.replace(tzinfo=None) - datetime.now()
        assert await panel.sync_clock(datetime.now(tz)) is False
    finally:
        await panel.stop()
