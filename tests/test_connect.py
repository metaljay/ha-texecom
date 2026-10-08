"""Connect protocol and driver tests against the fake panel."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import _paths  # noqa: F401
import pytest
from fake_connect_panel import FakeConnectPanel

from custom_components.texecom.connect import protocol as P
from custom_components.texecom.connect.client import ConnectClient, LoginRejected, PanelBusyError, Timing
from custom_components.texecom.connect.discovery import probe
from custom_components.texecom.connect.panel import ConnectPanel
from custom_components.texecom.panel import (
    ARMED_AWAY,
    ARMED_HOME,
    ARMED_NIGHT,
    ARMING,
    DISARMED,
    PENDING,
    TRIGGERED,
    PanelError,
)

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
    from custom_components.texecom.connect.discovery import discover

    info, zones, areas = await discover(client)
    await client.close()
    timing = kwargs.pop("timing", FAST)
    opts = {"part_arms": {"night": 1, "home": 0}, **kwargs}
    panel = ConnectPanel(
        "127.0.0.1", fake.port, "1234", info=info, zones=zones, areas=areas, timing=timing, reconnect_min=0.05, **opts
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


def test_display_message_drops_the_clock():
    assert P.display_message("HOME 13:48.52 Wed 07") == "HOME"
    assert P.display_message("HOME Wed 07 Oct 2026") == "HOME"
    assert P.display_message("System Alerts! 14:03.17 Wed 07") == "System Alerts!"
    assert P.display_message("Panel Lid Tamper 14:04.19 Wed 07") == "Panel Lid Tamper"
    assert P.display_message("Exit Time 10") == "Exit Time 10"


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
        assert panel.extra["display"] == "Premier Elite"
        fake.set_zone(1, 1)
        await wait_for(lambda: panel.zones[1].active)
        fake.set_zone(1, 2)
        await wait_for(lambda: panel.zones[1].tampered)
    finally:
        await panel.stop()


async def test_ready_to_arm_follows_the_panel(fake, monkeypatch):
    from custom_components.texecom.connect import panel as connect_panel

    monkeypatch.setattr(connect_panel, "READY_CHECK_DELAY", 0.05)
    panel = await make_panel(fake)
    try:
        assert panel.reports_ready and panel.areas[1].ready is True  # disarmed, nothing open
        fake.set_zone(2, 1)  # a door opens: re-read once zones settle
        await wait_for(lambda: panel.areas[1].ready is False)
        fake.set_zone(2, 0)
        await wait_for(lambda: panel.areas[1].ready is True)
        fake.ready = False  # whatever the panel says goes
        fake.set_zone(3, 0)
        await wait_for(lambda: panel.areas[1].ready is False)
    finally:
        await panel.stop()


async def test_zones_changing_dont_hold_up_the_regular_reads(fake, monkeypatch):
    """Re-reading "ready" as zones change mustn't put off the regular reads
    (the mains coming back is only seen in the power reading), as it did
    while people moved about in front of the detectors."""
    from custom_components.texecom.connect import panel as connect_panel

    monkeypatch.setattr(connect_panel, "READY_CHECK_DELAY", 0.05)
    panel = await make_panel(fake, timing=Timing(command_timeout=0.3, command_attempts=2, keepalive=0.5, login_delay=0))
    try:
        fake.on_battery = True
        fake.send_log(47, 9, 0, areas=0)  # AC Fail
        await wait_for(lambda: panel.extra.get("faults") == {"AC Fail"})
        fake.on_battery = False  # mains back: not logged, only in the readings
        loop = asyncio.get_running_loop()
        end = loop.time() + 3
        while panel.extra.get("faults") and loop.time() < end:
            fake.set_zone(3, 1)  # someone walking about, disarmed
            await asyncio.sleep(0.1)
            fake.set_zone(3, 0)
            await asyncio.sleep(0.1)
        assert panel.extra["faults"] == set()
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


async def test_a_refused_request_doesnt_name_home_assistant(fake):
    """Home Assistant is named as who armed or disarmed only when the panel
    took the request: after a refusal, the next change (a fob, say) isn't."""
    from fake_connect_panel import run_command

    panel = await make_panel(fake)
    try:
        run_command(fake, "refuse arm")
        with pytest.raises(PanelError, match="refused"):
            await panel.arm(1, "away")
        run_command(fake, "area armed")
        await wait_for(lambda: panel.areas[1].state == ARMED_AWAY)
        assert panel.areas[1].changed_by is None
        run_command(fake, "refuse disarm")
        with pytest.raises(PanelError, match="refused"):
            await panel.disarm(1)
        run_command(fake, "area off")
        await wait_for(lambda: panel.areas[1].state == DISARMED)
        assert panel.areas[1].changed_by is None
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


async def test_switching_mode_never_shows_disarmed(fake):
    panel = await make_panel(fake, part_arms={"night": 1, "home": 0})
    seen = []
    try:
        await panel.arm(1, "night")
        await wait_for(lambda: panel.areas[1].state == ARMED_NIGHT)
        panel.add_listener(lambda: seen.append(panel.areas[1].state))
        await panel.arm(1, "away")
        await wait_for(lambda: panel.areas[1].state == ARMED_AWAY)
        assert DISARMED not in seen
    finally:
        await panel.stop()


async def test_a_mode_switch_hides_only_its_own_disarm(fake):
    """Switching mode hides the panel's one brief "disarmed", not a real
    disarm: a panel with no exit time arms straight away, and a disarm just
    after shows at once."""
    panel = await make_panel(fake)
    try:
        await panel.arm(1, "night")
        await wait_for(lambda: panel.areas[1].state == ARMED_NIGHT)
        fake.exit_delay = 0
        await panel.arm(1, "away")
        await wait_for(lambda: panel.areas[1].state == ARMED_AWAY)
        fake.set_area(0)  # disarmed at a keypad straight after
        await wait_for(lambda: panel.areas[1].state == DISARMED)
    finally:
        await panel.stop()


async def test_a_reread_of_the_old_mode_doesnt_end_a_mode_switch(fake):
    """A re-read the panel answered before it disarmed for the switch still
    shows the old mode: the switch's own "disarmed" must stay hidden."""
    panel = await make_panel(fake)
    try:
        await panel.arm(1, "night")
        await wait_for(lambda: panel.areas[1].state == ARMED_NIGHT)
        panel._switching[1] = time.monotonic() + 10  # as arm() does when switching to Away
        panel._apply_area(1, "part armed", 1)  # the re-read
        panel._apply_area(1, "disarmed", None)  # the switch's own disarm
        assert panel.areas[1].state == ARMED_NIGHT
        panel._apply_area(1, "in exit", None)
        assert panel.areas[1].state == ARMING
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


async def test_an_alarm_stays_an_alarm_when_the_panel_goes_back_to_entry(fake):
    """After an alarm a real panel reported "in entry" again (the entry zone
    seen again) while the sirens sounded, then "in alarm" again: Home
    Assistant shows the alarm throughout, until it's disarmed."""
    from fake_connect_panel import run_command

    panel = await make_panel(fake)
    seen = []
    try:
        run_command(fake, "area armed")
        await wait_for(lambda: panel.areas[1].state == ARMED_AWAY)
        run_command(fake, "area entry")
        await wait_for(lambda: panel.areas[1].state == PENDING)
        panel.add_listener(lambda: seen.append(panel.areas[1].state))
        run_command(fake, "area alarm")
        await wait_for(lambda: panel.areas[1].state == TRIGGERED)
        run_command(fake, "area entry")
        await panel.refresh_areas()  # a re-read meanwhile says "in entry" too
        assert panel.areas[1].state == TRIGGERED
        run_command(fake, "area alarm")
        run_command(fake, "area off")
        await wait_for(lambda: panel.areas[1].state == DISARMED)
        assert PENDING not in seen
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
        fake.send_log(4, 0x83, 2)  # logged again once reported
        await wait_for(lambda: events)
        await asyncio.sleep(0.1)
        assert events == [("zone_alarm", {"zone": 2, "zone_name": "Lounge", "tamper": False})]
    finally:
        await panel.stop()


async def test_arm_failed_names_the_zone(fake):
    events = []
    panel = await make_panel(fake, on_event=lambda t, d: events.append((t, d)))
    try:
        fake.send_log(85, 0, 3)  # ARM_FAILED, zone 3 active at the end of the exit time
        await wait_for(lambda: events)
        assert events[0] == ("arm_failed", {"areas": 1, "zone": 3, "zone_name": "Kitchen"})
    finally:
        await panel.stop()


async def test_alarm_names_the_zone_from_its_alarmed_flag(fake):
    panel = await make_panel(fake)
    try:
        fake.set_area(3)
        await wait_for(lambda: panel.areas[1].state == ARMED_AWAY)
        fake.set_area(5)
        fake.set_zone(3, 0x11)  # active + alarmed, as the panel sends it
        await wait_for(lambda: panel.areas[1].state == TRIGGERED and panel.areas[1].changed_by == "Kitchen")
    finally:
        await panel.stop()


async def test_system_tampers(fake):
    events = []
    panel = await make_panel(fake, on_event=lambda t, d: events.append((t, d)))
    try:
        fake.send_log(60, 11, 0, areas=0)  # lid off
        fake.send_log(62, 11, 0, areas=0)  # a detector on the aux tamper circuit
        await wait_for(lambda: panel.extra.get("tampers") == {"Panel Box Tamper", "Auxiliary Tamper"})
        assert events[0] == ("tamper", {"source": "Panel Box Tamper", "log_type": 60})
        fake.send_log(60, 12, 0, areas=0)  # lid back on
        await wait_for(lambda: panel.extra.get("tampers") == {"Auxiliary Tamper"})
    finally:
        await panel.stop()


async def test_simulated_panel_commands(fake):
    """The --commands mode of the simulated panel, used to try things on a test
    Home Assistant: each command reaches the driver as a real panel's would."""
    from fake_connect_panel import run_command

    events = []
    panel = await make_panel(fake, on_event=lambda t, d: events.append((t, d)))
    try:
        assert run_command(fake, "zone 2 open") == "ok: zone 2 open"
        await wait_for(lambda: panel.zones[2].active)
        run_command(fake, "lid open")
        await wait_for(lambda: panel.extra.get("tampers") == {"Panel Box Tamper"})
        run_command(fake, "Mains Off")
        await wait_for(lambda: panel.extra.get("faults") == {"AC Fail"})
        run_command(fake, "user 3")
        run_command(fake, "armfail 3")
        await wait_for(lambda: [t for t, _d in events] == ["tamper", "fault", "user", "arm_failed"])
        run_command(fake, "area part 2")
        await wait_for(lambda: panel.areas[1].state == ARMED_HOME)
        run_command(fake, "refuse disarm")
        with pytest.raises(PanelError, match="refused"):
            await panel.disarm(1)
        assert run_command(fake, "zone two open") == "? numbers only: 'zone two open'"
        assert run_command(fake, "sound the bells").startswith("? 'sound the bells'\nCommands")
    finally:
        await panel.stop()


def test_flag_and_log_names():
    flags = bytearray(72)
    flags[P.FLAG_ARMED] = flags[P.FLAG_FULL_ARMED] = 0b01  # area 1
    flags[39] = 0b10  # area 2: chime enabled
    assert P.area_flags_set(bytes(flags), 1, 24) == [21, 22]
    assert P.flag_names([21, 22]) == ["21 Armed", "22 Full Armed"]
    assert P.flag_names(P.area_flags_set(bytes(flags), 2, 24)) == ["39 Chime Enabled"]
    assert len(P.AREA_FLAG_NAMES) == 73 and P.AREA_FLAG_NAMES[16] == "Ready"
    m = {"type": 85, "group": 0, "parameter": 3, "areas": 1}
    assert P.describe_log(m) == "Arm Failed (85), group Not Reported (0), parameter 3, areas 0x1"
    assert P.describe_log({**m, "type": 137}).startswith("unknown (137)")


async def test_diagnostics_name_the_flags_and_read_the_system_flags(fake, caplog):
    caplog.set_level("DEBUG", logger="custom_components.texecom")
    fake.system_flags = bytes([0, 4, 0, 0, 0, 0, 0, 0])
    panel = await make_panel(fake)
    try:
        fake.set_area(3)
        await wait_for(lambda: panel.areas[1].state == ARMED_AWAY)
        await panel.refresh_areas()
        diagnostics = await panel.async_diagnostics()
        assert diagnostics["area_flags"] == {1: ["21 Armed", "22 Full Armed"]}
        assert diagnostics["system_flags"] == "00 04 00 00 00 00 00 00"
        assert "area 1 flags: 21 Armed, 22 Full Armed" in caplog.text
        # A panel that doesn't answer it: diagnostics say so, and the session carries on.
        connections = fake.connections
        fake.ignore_next[P.CMD_GET_SYSTEM_FLAGS] = 1
        diagnostics = await panel.async_diagnostics()
        assert diagnostics["system_flags"].startswith("unreadable")
        assert panel.connected and fake.connections == connections
    finally:
        await panel.stop()


async def test_mains_fault_and_restore(fake):
    events = []
    panel = await make_panel(fake, on_event=lambda t, d: events.append((t, d)))
    try:
        fake.send_log(47, 9, 0, areas=0)  # AC Fail, maintenance alarm
        await wait_for(lambda: panel.extra.get("faults") == {"AC Fail"})
        fake.send_log(47, 10, 0, areas=0)  # restored
        await wait_for(lambda: panel.extra.get("faults") == set())
        assert [t for t, _d in events] == ["fault", "fault_cleared"]
    finally:
        await panel.stop()


async def test_mains_restore_comes_from_power_readings(fake):
    events = []
    panel = await make_panel(fake, on_event=lambda t, d: events.append((t, d)))
    try:
        fake.on_battery = True
        fake.send_log(47, 9, 0, areas=0)  # AC Fail; this panel never logs the restore
        await wait_for(lambda: panel.extra.get("faults") == {"AC Fail"})
        await panel.read_power()
        assert panel.extra["faults"] == {"AC Fail"}
        fake.on_battery = False
        await panel.read_power()
        assert panel.extra["faults"] == set()
        assert [t for t, _d in events] == ["fault", "fault_cleared"]
        fake.on_battery = True  # e.g. after a restart, with no log entry at all
        await panel.read_power()
        assert panel.extra["faults"] == {"AC Fail"}
    finally:
        await panel.stop()


async def test_mains_from_a_real_panels_readings(fake):
    """Readings from a Premier Elite 24 with its mains switched off: both
    currents read 0 mA on battery (13.1 V falling to 12.2 V), against 297 and
    18 mA at 13.63 V on mains. After a restart on battery, 13.07 V at 0 mA
    could be a panel that never reports current, so only a clearly low
    voltage counts; once current has been seen, 0 mA is enough."""
    mains, battery, battery_low = bytes([100, 99, 98, 33, 2]), bytes([100, 91, 92, 0, 0]), bytes([100, 79, 81, 0, 0])
    events = []
    fake.power_override = battery
    panel = await make_panel(fake, on_event=lambda t, d: events.append((t, d)))
    try:
        assert panel.extra["power"].panel_voltage == pytest.approx(13.07)
        assert not panel.extra.get("faults")
        fake.power_override = battery_low
        await panel.read_power()
        assert panel.extra["faults"] == {"AC Fail"}
        fake.power_override = mains
        await panel.read_power()
        power = panel.extra["power"]
        assert (power.panel_current, power.battery_current, power.panel_voltage) == (297, 18, pytest.approx(13.63))
        assert panel.extra["faults"] == set()
        fake.power_override = battery
        await panel.read_power()
        assert panel.extra["faults"] == {"AC Fail"}
        assert [t for t, _d in events] == ["fault", "fault_cleared", "fault"]
    finally:
        await panel.stop()


async def test_a_remote_psu_mains_failure_waits_for_its_own_restore(fake):
    """PSU AC Fail is a remote power supply losing its mains: the panel's own
    healthy readings say nothing about it, so only its restore clears it."""
    events = []
    panel = await make_panel(fake, on_event=lambda t, d: events.append((t, d)))
    try:
        fake.send_log(107, 9, 0, areas=0)  # PSU AC Fail
        await wait_for(lambda: panel.extra.get("faults") == {"PSU AC Fail"})
        await panel.read_power()  # the panel itself is on mains
        assert panel.extra["faults"] == {"PSU AC Fail"}
        fake.send_log(107, 10, 0, areas=0)  # the remote PSU's mains is back
        await wait_for(lambda: panel.extra.get("faults") == set())
        assert [t for t, _d in events] == ["fault", "fault_cleared"]
    finally:
        await panel.stop()


async def test_clock_drift_is_reported_when_not_syncing(fake):
    drifts = []
    fake.clock_offset = timedelta(days=-700)
    panel = await make_panel(fake, on_clock_drift=drifts.append)
    try:
        await wait_for(lambda: drifts)
        assert drifts[0] < -700 * 86400 + 120
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


def test_nice_names_keep_acronyms():
    from custom_components.texecom.panel import nice_name

    assert nice_name("HALL PIR") == "Hall PIR"
    assert nice_name("HOUSE") == "House"
    assert nice_name("Front Door") == "Front Door"  # already mixed case: left alone
