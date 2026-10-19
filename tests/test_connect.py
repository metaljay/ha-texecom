"""Connect protocol and driver tests against the fake panel."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import _paths  # noqa: F401
import pytest
from fake_connect_panel import FAKE_USERS, FakeConnectPanel, load_diagnostics, user_record

from custom_components.texecom.connect import capture
from custom_components.texecom.connect import protocol as P
from custom_components.texecom.connect.client import ConnectClient, ConnectError, LoginRejected, PanelBusyError, Timing
from custom_components.texecom.connect.discovery import probe
from custom_components.texecom.connect.panel import ConnectPanel
from custom_components.texecom.panel import (
    ARMED_AWAY,
    ARMED_HOME,
    ARMED_NIGHT,
    ARMED_VACATION,
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


def test_a_user_record_gives_only_the_name():
    assert P.decode_user_name(user_record("Sam", "864208")) == "Sam"
    assert P.decode_user_name(user_record("", "")) == ""  # a user without a name
    assert P.decode_user_name(bytes([P.NAK])) is None  # no such user


def test_a_reply_with_a_bad_crc_isnt_logged():
    """A reply can hold a user's code (GET_USER), so only messages are shown in full."""
    errors: list[str] = []
    parser = P.FrameParser(lambda _f: None, on_error=errors.append)
    for frame_type in (P.TYPE_RESPONSE, P.TYPE_MESSAGE):
        bad = bytearray(P.encode_frame(frame_type, 1, bytes([P.CMD_GET_USER]) + user_record("Sam", "864208")))
        bad[-1] ^= 0xFF
        parser.push(bytes(bad))
    assert len(errors) == 2
    assert "864208" not in errors[0] and "bytes" in errors[0]
    assert "864208" in errors[1]  # an event message (which never holds a code) is shown


def test_area_flags_decoding():
    flags = bytearray(72)
    flags[P.FLAG_ARMED] = flags[P.FLAG_PART_ARMED] = flags[P.FLAG_PART_ARM_2] = 1
    assert P.decode_area_flags(bytes(flags), [1, 2], 24) == {1: ("part armed", 2), 2: ("disarmed", None)}
    flags = bytearray(72)
    flags[P.FLAG_PART_ARMING] = 1
    assert P.decode_area_flags(bytes(flags), [1], 24)[1] == ("in exit", None)


def test_area_flags_from_a_real_part_arm():
    """Read from a Premier Elite 24 (V6.05.03) on 14 Oct 2026, part arming
    with Part Arm 3 at the keypad. Part Arming (24) stayed set all the time
    it was armed: only Armed (21) tells the exit delay and armed apart."""

    def decode(*names: int) -> tuple[str, int | None]:
        flags = bytearray(72)
        for flag in names:
            flags[flag] = 1
        return P.decode_area_flags(bytes(flags), [1], 24)[1]

    common = (16, 25, 29, 31, 32)  # Ready, Force Armable, Bell SCB, Detector Latch, Detector Reset
    assert decode(*common, 19, 20, 24, 52) == ("in exit", 3)  # the exit delay, arming to Part Arm 3
    assert decode(*common, 21, 24, 52) == ("part armed", 3)  # just armed
    assert decode(*common, 21, 23, 24, 52, 60) == ("part armed", 3)  # a minute later
    assert decode(21, 22, 19) == ("armed", None)  # a stale Exit flag just after a full arm


def test_area_flags_say_what_an_exit_delay_is_arming_to():
    """Read from a Premier Elite 24 (V6.05.03) on 14 Oct 2026, arming at the
    keypad, where the panel's messages are the same for every mode. In an
    exit delay the second value is what the area is arming to: a part arm,
    or 0 for a full arm. Part Arming (24) and the part arm's flag came with
    Exit (19); a full arm set Full Armed/Exit (63) about 1.5 s later."""

    def decode(*names: int) -> tuple[str, int | None]:
        flags = bytearray(72)
        for flag in names:
            flags[flag] = 1
        return P.decode_area_flags(bytes(flags), [1], 24)[1]

    assert decode(16, 19, 24, 25, 29, 31, 50) == ("in exit", 1)  # Part Arm 1
    assert decode(16, 19, 20, 24, 25, 29, 31, 51) == ("in exit", 2)  # Part Arm 2
    assert decode(16, 19, 25, 29, 31) == ("in exit", None)  # a full arm, 0.7 s in: not known yet
    assert decode(16, 19, 20, 25, 29, 31, 62, 63) == ("in exit", 0)  # the same, 1.6 s in


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


async def test_a_disarm_doesnt_flick_back_to_armed(fake):
    """The area flags can still show armed just after the panel announces a
    disarm (seen on a real panel: 0.35 s later); the re-read after a disarm
    mustn't put the alarm back to armed for a few seconds."""
    panel = await make_panel(fake)
    try:
        fake.set_area(3)
        await wait_for(lambda: panel.areas[1].state == ARMED_AWAY)
        fake.flags_lag = 1.5
        seen: list[str] = []
        panel.add_listener(lambda: seen.append(panel.areas[1].state))
        fake.set_area(0)
        await wait_for(lambda: panel.areas[1].state == DISARMED)
        await asyncio.sleep(1.0)  # the re-read 0.5 s after the disarm, while the flags lag
        assert ARMED_AWAY not in seen[seen.index(DISARMED) :]
        await panel.refresh_areas()  # later, the flags have caught up
        assert panel.areas[1].state == DISARMED
    finally:
        await panel.stop()


async def test_an_arm_with_no_code_doesnt_keep_who_disarmed(fake):
    """An area that goes straight from disarmed to armed with no code (a fob,
    or no exit time) doesn't keep the name of whoever disarmed it (F4)."""
    panel = await make_panel(fake)
    try:
        fake.set_area(3)
        await wait_for(lambda: panel.areas[1].state == ARMED_AWAY)
        fake.send_user(4)
        fake.set_area(0)
        await wait_for(lambda: panel.areas[1].state == DISARMED)
        assert panel.areas[1].changed_by == "User 4"
        fake.set_area(3)
        await wait_for(lambda: panel.areas[1].state == ARMED_AWAY)
        assert panel.areas[1].changed_by is None
    finally:
        await panel.stop()


async def test_an_alarm_never_names_a_user(fake):
    """A code entered shortly before an alarm doesn't name that user as the
    alarm's cause while no zone has been reported."""
    panel = await make_panel(fake)
    try:
        fake.send_user(1)
        await asyncio.sleep(0.2)
        fake.set_area(5)
        await wait_for(lambda: panel.areas[1].state == TRIGGERED)
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
    """Home has no part arm here, so it can't be confused with one that has."""
    panel = await make_panel(fake, part_arms={"night": 1, "home": 0})
    try:
        fake.send_user(3)
        fake.send_log(79, 0, 0)  # PART_ARM_2 log
        fake.set_area(4, 2)
        await wait_for(lambda: panel.areas[1].state == ARMED_HOME and panel.areas[1].part_arm == 2)
        assert panel.areas[1].changed_by == "User 3"
    finally:
        await panel.stop()


async def test_keypad_part_arm_not_mapped_never_looks_like_a_mapped_one(fake):
    """Issue #32: with Home on part arm 2, part arm 3 (no button) used to show
    as Home too. Now it shows as a mode no part arm uses: Vacation."""
    panel = await make_panel(fake, part_arms={"night": 1, "home": 2})
    try:
        fake.send_log(80, 0, 0)  # PART_ARM_3 log
        fake.set_area(4, 3)
        await wait_for(lambda: panel.areas[1].state == ARMED_VACATION and panel.areas[1].part_arm == 3)
    finally:
        await panel.stop()


async def test_all_three_part_arms_can_have_a_button(fake):
    """Night, Home, Vacation and Custom bypass can each use a part arm (issue #32)."""
    panel = await make_panel(fake, part_arms={"night": 1, "home": 3, "vacation": 2})
    try:
        assert panel.offered_modes == ["away", "home", "night", "vacation"]
        await panel.arm(1, "vacation")
        assert (P.CMD_ARM_AREA, bytes([2, 1])) in fake.commands  # part arm 2, area 1
        await wait_for(lambda: panel.areas[1].state == ARMED_VACATION and panel.areas[1].part_arm == 2)
        await panel.disarm(1)
        await wait_for(lambda: panel.areas[1].state == DISARMED)
        fake.send_log(80, 0, 0)  # PART_ARM_3 log, at the keypad
        fake.set_area(4, 3)
        await wait_for(lambda: panel.areas[1].state == ARMED_HOME and panel.areas[1].part_arm == 3)
        with pytest.raises(PanelError, match="custom_bypass isn't set up"):
            await panel.arm(1, "custom_bypass")
    finally:
        await panel.stop()


async def test_connecting_while_part_armed_with_part_arming_still_set(fake):
    """Seen on a real panel: connecting while part armed showed Arming… until
    the next disarm, because Part Arming stays set (14 Oct 2026)."""
    fake.sticky_part_arming = True
    fake.set_area(4, 3)
    panel = await make_panel(fake, part_arms={"night": 1, "home": 2})
    try:
        await wait_for(lambda: panel.areas[1].state == ARMED_VACATION and panel.areas[1].part_arm == 3)
    finally:
        await panel.stop()


async def test_a_keypad_arm_says_what_its_arming_to(fake):
    """Discussion #38: while arming, the alarm says which mode it's arming
    to, from the flags (the exit delay's messages don't say)."""
    from fake_connect_panel import run_command

    panel = await make_panel(fake, part_arms={"night": 1, "home": 2})
    try:
        run_command(fake, "area exit part 1")
        await wait_for(lambda: panel.areas[1].arming_to == ARMED_NIGHT)
        assert panel.areas[1].state == ARMING
        run_command(fake, "area off")
        await wait_for(lambda: panel.areas[1].state == DISARMED)
        assert panel.areas[1].arming_to is None
        run_command(fake, "area exit full")
        await wait_for(lambda: panel.areas[1].arming_to == ARMED_AWAY)
        run_command(fake, "area armed")
        await wait_for(lambda: panel.areas[1].state == ARMED_AWAY)
        assert panel.areas[1].arming_to is None
    finally:
        await panel.stop()


async def test_the_flags_are_read_again_until_they_say_what_its_arming_to(fake):
    """A full arm's flag came about 1.5 s after the exit delay started; a
    panel whose flags never say isn't read over and over."""
    panel = await make_panel(fake)
    try:
        fake.set_area(1)  # the flags don't say yet
        await wait_for(lambda: panel.areas[1].state == ARMING)
        await asyncio.sleep(0.8)
        assert panel.areas[1].arming_to is None
        fake.part_arms[1] = 0  # now they say: a full arm
        await wait_for(lambda: panel.areas[1].arming_to == ARMED_AWAY)
        fake.set_area(0)
        await wait_for(lambda: panel.areas[1].state == DISARMED)
        fake.set_area(1)  # and this time they never say
        await asyncio.sleep(4)
        reads = sum(1 for cmd, _ in fake.commands if cmd == P.CMD_GET_AREA_FLAGS)
        await asyncio.sleep(1)
        assert sum(1 for cmd, _ in fake.commands if cmd == P.CMD_GET_AREA_FLAGS) == reads
        assert panel.areas[1].arming_to is None
    finally:
        await panel.stop()


async def test_arming_from_home_assistant_says_what_its_arming_to_at_once(fake):
    fake.exit_delay = 1
    panel = await make_panel(fake)
    try:
        await panel.arm(1, "night")
        await wait_for(lambda: panel.areas[1].state == ARMING)
        assert panel.areas[1].arming_to == ARMED_NIGHT
        await wait_for(lambda: panel.areas[1].state == ARMED_NIGHT)
        assert panel.areas[1].arming_to is None
    finally:
        await panel.stop()


async def test_stale_exit_flag_is_ignored_while_armed(fake):
    panel = await make_panel(fake)
    try:
        fake.set_area(3)
        await wait_for(lambda: panel.areas[1].state == ARMED_AWAY)
        panel._apply_area(1, "in exit", None, from_flags=True)
        assert panel.areas[1].state == ARMED_AWAY
    finally:
        await panel.stop()


async def test_an_exit_message_while_armed_shows_arming(fake):
    """The panel's own message is believed, in case its "disarmed" was missed."""
    panel = await make_panel(fake)
    try:
        fake.set_area(3)
        await wait_for(lambda: panel.areas[1].state == ARMED_AWAY)
        panel._apply_area(1, "in exit", None)
        assert panel.areas[1].state == ARMING
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


def single_flag_client(replies: dict[int, list[bytes]]) -> ConnectClient:
    """A client reading area flags one at a time, answered from `replies`
    (flag -> replies in turn; 0x00 for any other flag)."""
    client = ConnectClient("127.0.0.1", 1, "1234")
    client.single_flag_reads = True

    async def command(cmd, body=b"", *, optional=False):
        return replies[body[0]].pop(0) if replies.get(body[0]) else b"\x00"

    client.command = command
    return client


async def test_a_busy_nak_is_not_read_as_alarms_on_an_8_area_panel():
    """An Elite 88 has 8 areas in 1-byte bitmaps, so a NAK (0x15) is also a
    valid answer: areas 1, 3 and 5. A NAK followed by the real answer must
    not set those areas off."""
    client = single_flag_client({P.FLAG_ALARM: [bytes([P.NAK]), b"\x00"]})
    states = await client.area_states([1, 2, 3, 4, 5], 88)
    assert all(state == "disarmed" for state, _part in states.values())


async def test_the_same_answer_twice_is_believed_on_an_8_area_panel():
    client = single_flag_client({P.FLAG_FULL_ARMED: [bytes([0x15]), bytes([0x15])]})
    states = await client.area_states([1, 2, 3, 4, 5], 88)
    assert [states[a][0] for a in (1, 2, 3, 4, 5)] == ["armed", "disarmed", "armed", "disarmed", "armed"]


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


async def test_the_simulated_smartcom_is_busy_after_an_alarm(fake):
    """As a real SmartCom: a moment after the disarm that follows an alarm it
    hangs up to report the alarm, and turns logins away for a while (with
    frames the driver doesn't know, then closing the connection)."""
    from fake_connect_panel import run_command

    run_command(fake, "busy 0.6")
    fake.report_after = 0.05
    panel = await make_panel(fake)
    try:
        run_command(fake, "area alarm")
        await wait_for(lambda: panel.areas[1].state == TRIGGERED)
        connections = fake.connections
        run_command(fake, "area off")
        await wait_for(lambda: not panel.connected)
        await asyncio.sleep(0.4)
        assert not panel.connected and fake.connections > connections  # tried, and turned away
        assert "closed the connection" in panel.last_error
        await wait_for(lambda: panel.connected)  # let back in afterwards
        assert panel.areas[1].state == DISARMED
    finally:
        await panel.stop()


async def test_a_smartcom_busy_reporting_an_alarm_is_no_warning_until_entities_go_unavailable(fake, caplog):
    """After an alarm a SmartCom turns Home Assistant away for about two
    minutes (92 s once): expected, so retries are quiet until it's been as
    long as entities keep their state (3 minutes), then one warning."""
    from fake_connect_panel import run_command

    def warnings() -> list[str]:
        return [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]

    async def alarm_then_disarm() -> None:
        run_command(fake, "area alarm")
        await wait_for(lambda: panel.areas[1].state == TRIGGERED)
        run_command(fake, "area off")
        await wait_for(lambda: not panel.connected)
        await wait_for(lambda: panel.connected, timeout=5)

    caplog.set_level("DEBUG", logger="custom_components.texecom")
    run_command(fake, "busy 1")
    fake.report_after = 0.05
    panel = await make_panel(fake)
    panel.offline_grace = 3.0  # the 3 minutes, shortened
    try:
        connections = fake.connections
        await alarm_then_disarm()
        assert fake.connections - connections >= 5  # four logins turned away, then back in
        assert warnings() == []
        panel.offline_grace = 0.3  # now longer than that
        await alarm_then_disarm()
        assert len(warnings()) == 1 and "still trying" in warnings()[0]
    finally:
        await panel.stop()


async def test_a_login_turned_away_is_not_logged_as_a_session_ending(fake, caplog):
    """A busy SmartCom turns logins away for minutes, and each one ended
    with an INFO line about a session that never started."""
    caplog.set_level("DEBUG", logger="custom_components.texecom")
    fake.busy_until = asyncio.get_running_loop().time() + 5
    client = ConnectClient("127.0.0.1", fake.port, "1234", timing=FAST)
    with pytest.raises(ConnectError):
        await client.connect()
    assert not [r for r in caplog.records if r.levelname == "INFO" and "session ended" in r.getMessage()]


async def test_the_panels_own_conditions_are_no_warnings(fake, caplog, quick_internal_alarm):
    """Issue #45: Home Assistant shows a custom integration's warnings as
    faults to report. Tampers, faults, mains off, alarms, a failed arm and an
    impossible clock are the panel's state, which entities and notices
    already show, so they log at INFO."""
    from fake_connect_panel import run_command

    caplog.set_level("DEBUG", logger="custom_components.texecom")
    events, drifts = [], []
    fake.clock_raw = bytes(6)  # day 0, month 0
    panel = await make_panel(fake, on_event=lambda t, d: events.append(t), on_clock_drift=drifts.append)
    try:
        run_command(fake, "aux open unlogged")  # the internal alarm, with nothing logged
        await panel.refresh_areas()
        await wait_for(lambda: panel.extra.get("tampers") == {"Internal Alarm"})
        fake.send_log(60, 11, 0, areas=0)  # lid off
        fake.send_log(47, 9, 0, areas=0)  # AC Fail, from the log
        fake.send_log(85, 0, 3)  # arming failed: zone 3 active
        run_command(fake, "zone 3 alarm")
        await wait_for(lambda: events.count("tamper") == 2 and {"fault", "arm_failed", "zone_alarm"} <= set(events))
        fake.send_log(47, 10, 0, areas=0)  # mains back
        await wait_for(lambda: "fault_cleared" in events)
        fake.on_battery = True  # mains off again, this time from the power readings
        await panel.read_power()
        await panel.read_power()
        assert events.count("fault") == 2 and drifts == [None]
    finally:
        await panel.stop()
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []
    info = "\n".join(r.getMessage() for r in caplog.records if r.levelno == logging.INFO)
    for line in (
        "internal alarm is on",
        "Panel Box Tamper",
        "AC Fail",
        "arming failed",
        "zone 3 (Kitchen) in alarm",
        "running on battery",
        "panel clock reads",
    ):
        assert line in info


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


async def test_a_zone_seen_later_in_an_alarm_doesnt_replace_the_one_that_set_it_off(fake, monkeypatch):
    """However long the alarm lasts: the hallway sets it off and the kitchen
    sees someone over a minute later; the alarm still names the hallway,
    also when the panel says "in alarm" again."""
    from custom_components.texecom.connect import events
    from custom_components.texecom.connect import panel as connect_panel

    for module in (events, connect_panel):
        monkeypatch.setattr(module, "USER_CHANGE_WINDOW", 0.2)  # the minute, shortened
    panel = await make_panel(fake)
    try:
        fake.set_area(3)
        await wait_for(lambda: panel.areas[1].state == ARMED_AWAY)
        fake.set_area(5)
        fake.set_zone(1, 0x11)  # Hallway: active and alarmed
        await wait_for(lambda: panel.areas[1].state == TRIGGERED and panel.areas[1].changed_by == "Hallway")
        await asyncio.sleep(0.3)
        fake.set_zone(3, 0x11)  # Kitchen, later
        fake.send_zone_alarm(3)
        await asyncio.sleep(0.1)
        await panel.refresh_areas()  # a re-read: still in alarm
        fake.set_area(5)  # the panel says "in alarm" again
        await asyncio.sleep(0.1)
        assert panel.areas[1].state == TRIGGERED and panel.areas[1].changed_by == "Hallway"
        fake.set_area(0)  # disarmed: the next alarm names its own zone
        await wait_for(lambda: panel.areas[1].state == DISARMED)
        fake.set_area(3)
        await wait_for(lambda: panel.areas[1].state == ARMED_AWAY)
        await asyncio.sleep(0.3)
        fake.set_area(5)
        fake.set_zone(3, 0x11)
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


@pytest.fixture
def quick_internal_alarm(monkeypatch):
    from custom_components.texecom.connect import conditions

    monkeypatch.setattr(conditions, "INTERNAL_ALARM_CONFIRM", 0.2)


async def test_a_tamper_the_panel_doesnt_log_shows_from_its_internal_alarm(fake, quick_internal_alarm):
    """Seen on a real panel: a detector's cover opened while disarmed, the
    internal sounder went off (area flag 44, Internal Alarm), but the panel
    didn't log the tamper; only its restore, when the cover closed. It shows
    as the tamper "Internal Alarm" until a code at the keypad clears it."""
    from fake_connect_panel import run_command

    events = []
    panel = await make_panel(fake, on_event=lambda t, d: events.append((t, d)))
    try:
        run_command(fake, "aux open unlogged")
        await panel.refresh_areas()  # the next regular read
        assert not panel.extra.get("tampers")  # not at once: it must stay on a moment
        await wait_for(lambda: panel.extra.get("tampers") == {"Internal Alarm"})  # read again by itself
        assert events == [("tamper", {"source": "Internal Alarm", "log_type": None})]
        run_command(fake, "aux closed")  # the restore of the tamper the panel didn't report
        await asyncio.sleep(0.1)
        await panel.refresh_areas()
        assert panel.extra["tampers"] == {"Internal Alarm"}  # the internal alarm is on until a code
        run_command(fake, "user 1")  # a code silences it: read again soon
        await wait_for(lambda: panel.extra.get("tampers") == set())
        assert events[-2:] == [
            ("user", {"user": 1, "method": "code"}),
            ("tamper_cleared", {"source": "Internal Alarm", "log_type": None}),
        ]
    finally:
        await panel.stop()


async def test_a_reported_tamper_doesnt_show_its_internal_alarm_too(fake, quick_internal_alarm, monkeypatch):
    """The internal alarm that goes with a logged tamper isn't a second
    tamper, before or after the tamper closes; the same cover opened again
    later, this time not logged, is shown."""
    from fake_connect_panel import run_command

    from custom_components.texecom.connect import conditions

    monkeypatch.setattr(conditions, "CAUSE_MARGIN", 0)  # the second opening isn't minutes later here
    panel = await make_panel(fake)
    try:
        run_command(fake, "aux open")  # logged, with the internal alarm
        await wait_for(lambda: panel.extra.get("tampers") == {"Auxiliary Tamper"})
        await panel.refresh_areas()
        await asyncio.sleep(0.3)
        await panel.refresh_areas()
        run_command(fake, "aux closed")
        await wait_for(lambda: panel.extra.get("tampers") == set())
        await asyncio.sleep(0.3)
        await panel.refresh_areas()  # the internal alarm is still on: explained by the tamper
        assert panel.extra["tampers"] == set()
        run_command(fake, "user 1")  # silenced
        await wait_for(lambda: not panel._internal_alarms[1].since)
        run_command(fake, "aux open unlogged")  # opened again; the panel doesn't log it this time
        await panel.refresh_areas()
        await wait_for(lambda: panel.extra.get("tampers") == {"Internal Alarm"})
    finally:
        await panel.stop()


async def test_the_internal_alarm_is_no_tamper_in_an_alarm_an_exit_or_a_failed_arm(fake, quick_internal_alarm):
    """Things that sound the internal sounders and are shown already: an
    alarm (and the disarm after it), an exit delay, the warning after a
    failed arm. A flag that's only on for a moment isn't shown either."""
    panel = await make_panel(fake)

    async def settle() -> None:
        await panel.refresh_areas()
        await asyncio.sleep(0.3)
        await panel.refresh_areas()

    try:
        fake.set_area(3)
        await wait_for(lambda: panel.areas[1].state == ARMED_AWAY)
        fake.set_area(5)
        fake.internal_alarm = True
        await wait_for(lambda: panel.areas[1].state == TRIGGERED)
        await settle()
        fake.set_area(0)  # disarmed; its internal alarm is still on until a code
        await wait_for(lambda: panel.areas[1].state == DISARMED)
        await settle()
        fake.send_user(1)
        await panel.refresh_areas()
        fake.set_area(1)  # an exit delay
        fake.internal_alarm = True
        await wait_for(lambda: panel.areas[1].state == ARMING)
        await settle()
        fake.send_log(85, 0, 3)  # the arm failed: Kitchen active at the end of the exit time
        fake.set_area(0)
        await wait_for(lambda: panel.areas[1].state == DISARMED)
        await settle()
        fake.send_user(1)
        await panel.refresh_areas()
        fake.internal_alarm = True  # on for a moment only
        await panel.refresh_areas()
        fake.internal_alarm = False
        await asyncio.sleep(0.4)  # the re-read finds it off
        await panel.refresh_areas()
        assert not panel.extra.get("tampers")
    finally:
        await panel.stop()


async def test_an_internal_alarm_already_on_when_connecting_is_shown(fake, quick_internal_alarm):
    """After a restart a tamper that was logged before is unknown, but the
    panel's internal alarm still says something's wrong."""
    fake.internal_alarm = True
    panel = await make_panel(fake)
    try:
        await wait_for(lambda: panel.extra.get("tampers") == {"Internal Alarm"})
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


async def test_simulated_zone_alarm_is_logged_as_a_real_panel_logs_it(fake):
    """`zone N alarm` sends the zone-alarm log too, typed as on a real panel:
    1 for the entry/exit zone, 3 for the others."""
    from fake_connect_panel import run_command

    logged, send_log = [], fake.send_log
    fake.send_log = lambda *args, **kwargs: (logged.append(args), send_log(*args, **kwargs))
    events = []
    panel = await make_panel(fake, on_event=lambda t, d: events.append((t, d)))
    try:
        run_command(fake, "zone 1 alarm")
        run_command(fake, "zone 3 alarm")
        await wait_for(lambda: len(events) == 2)
        assert logged == [(1, 3, 1), (3, 3, 3)]
        assert [d["zone_name"] for _t, d in events] == ["Hallway", "Kitchen"]
        assert panel.zones[3].active
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
        # One request missed (a panel busy with a burst of events can): asked again.
        fake.ignore_next[P.CMD_GET_SYSTEM_FLAGS] = 1
        diagnostics = await panel.async_diagnostics()
        assert diagnostics["system_flags"] == "00 04 00 00 00 00 00 00"
        # A panel that doesn't answer it: diagnostics say so, and the session carries on.
        connections = fake.connections
        fake.ignore_next[P.CMD_GET_SYSTEM_FLAGS] = 2
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
        assert panel.extra["faults"] == set()  # one reading can be a blip
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
        assert not panel.extra.get("faults")  # one reading can be a blip
        await panel.read_power()
        assert panel.extra["faults"] == {"AC Fail"}
        fake.power_override = mains
        await panel.read_power()
        power = panel.extra["power"]
        assert (power.panel_current, power.battery_current, power.panel_voltage) == (297, 18, pytest.approx(13.63))
        assert panel.extra["faults"] == set()
        fake.power_override = battery
        await panel.read_power()
        await panel.read_power()
        assert panel.extra["faults"] == {"AC Fail"}
        assert [t for t, _d in events] == ["fault", "fault_cleared", "fault"]
    finally:
        await panel.stop()


async def test_a_single_battery_reading_is_a_blip(fake):
    """Issue #18 and our own panel (12 Oct: 0 mA at 13.14 V with mains on,
    333 mA at 13.63 V 30 s later): one "on battery" reading between mains
    readings, or a garbled one, never shows mains off."""
    mains, blip, garbled = bytes([100, 99, 98, 33, 2]), bytes([100, 92, 92, 0, 0]), bytes([100, 0, 0, 0, 0])
    events = []
    fake.power_override = mains
    panel = await make_panel(fake, on_event=lambda t, d: events.append((t, d)))
    try:
        for reading in (blip, mains, blip, garbled, mains, garbled, garbled):
            fake.power_override = reading
            await panel.read_power()
        assert not panel.extra.get("faults")
        assert events == []
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


async def test_one_refused_login_is_tried_again(fake):
    """A panel that refuses a working code once (busy) mustn't make the user
    enter it again."""
    calls = []
    info, zones, areas = await probe("127.0.0.1", fake.port, "1234")
    fake.nak_next[P.CMD_LOGIN] = 1
    panel = ConnectPanel(
        "127.0.0.1",
        fake.port,
        "1234",
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
        await wait_for(lambda: panel.connected)
        assert calls == []
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


async def test_user_names_are_read_and_their_codes_dropped(fake, caplog):
    caplog.set_level("DEBUG", logger="custom_components.texecom")
    panel = await make_panel(fake)
    try:
        assert await panel.async_read_user_names() == {1: "Master", 3: "Sam"}
        # Users 1-24 asked once, then 25 twice (a NAK may only mean "busy").
        asked = [args[0] for cmd, args in fake.commands if cmd == P.CMD_GET_USER]
        assert asked == [*range(1, 26), 25]
        diagnostics = await panel.async_diagnostics()
        for _name, code in FAKE_USERS.values():
            assert code not in caplog.text and code not in str(diagnostics)
        assert panel.connected
    finally:
        await panel.stop()


async def test_reading_user_names_when_not_connected(fake):
    panel = await make_panel(fake)
    await panel.stop()
    with pytest.raises(PanelError):
        await panel.async_read_user_names()


# ─── More than one area ────────────────────────────────────────────────────


@pytest.fixture
async def two_areas():
    """A panel like the first one reported from outside (issue #4): an Elite
    12 on V6.03, two areas. The Garage is in area 2, GARAGE."""
    from fake_connect_panel import TWO_AREAS, with_garage_area

    panel = FakeConnectPanel(zones=with_garage_area(FakeConnectPanel().zone_list), areas=TWO_AREAS)
    panel.ident = b"Elite 12     V6.03.02LS1"
    await panel.start()
    yield panel
    await panel.close()


async def test_a_panel_with_two_areas(two_areas, monkeypatch):
    """Each area is read, shown, armed and ready to arm on its own."""
    from custom_components.texecom.connect import panel as connect_panel

    monkeypatch.setattr(connect_panel, "READY_CHECK_DELAY", 0.05)
    fake = two_areas
    info, zones, areas = await probe("127.0.0.1", fake.port, "1234")
    assert (info.zones, info.firmware) == (12, "V6.03.02LS1")
    assert [(a.number, a.name) for a in areas] == [(1, "HOUSE"), (2, "GARAGE")]
    assert {z.name: z.areas for z in zones}["Garage"] == [2]
    panel = await make_panel(fake)
    try:
        assert panel.areas[1].ready and panel.areas[2].ready
        await panel.arm(2, "away")
        assert (P.CMD_ARM_AREA, bytes([0, 0b10])) in fake.commands
        await wait_for(lambda: panel.areas[2].state == ARMED_AWAY)
        assert panel.areas[1].state == DISARMED
        fake.set_zone(2, 1)  # the lounge (area 1) opens
        await wait_for(lambda: panel.areas[1].ready is False)
        assert panel.areas[2].ready is False  # armed: not "ready to arm"
        await panel.disarm(2)
        await wait_for(lambda: panel.areas[2].state == DISARMED)
        assert panel.areas[1].state == DISARMED
    finally:
        await panel.stop()


async def test_each_area_names_the_zone_that_set_its_own_alarm_off(two_areas):
    """Two alarms within a minute: each area names its own zone, and
    disarming one doesn't change what the other says."""
    fake = two_areas
    panel = await make_panel(fake)
    try:
        fake.set_area(3, area=1)
        fake.set_area(3, area=2)
        await wait_for(lambda: panel.areas[1].state == panel.areas[2].state == ARMED_AWAY)
        fake.set_area(5, area=1)
        fake.set_zone(1, 0x11)  # Hallway (the house)
        await wait_for(lambda: panel.areas[1].state == TRIGGERED and panel.areas[1].changed_by == "Hallway")
        fake.set_zone(4, 0x11)  # Garage (the garage) seen first...
        fake.set_area(5, area=2)  # ...then its alarm
        await wait_for(lambda: panel.areas[2].state == TRIGGERED)
        await asyncio.sleep(0.1)
        assert panel.areas[2].changed_by == "Garage"
        fake.set_area(0, area=2)  # the garage disarmed
        await wait_for(lambda: panel.areas[2].state == DISARMED)
        fake.set_area(5, area=1)  # the house says "in alarm" again
        await asyncio.sleep(0.1)
        assert panel.areas[1].changed_by == "Hallway"
    finally:
        await panel.stop()


async def test_who_armed_or_disarmed_is_worked_out_for_each_area(two_areas):
    """Home Assistant arming the house doesn't explain the garage arming at
    the same time (a fob, say); one keypad code disarming both names the
    user on both."""
    fake = two_areas
    fake.exit_delay = 1.0
    panel = await make_panel(fake)
    try:
        await panel.arm(1, "away")  # the house: an exit delay
        await wait_for(lambda: panel.areas[1].state == ARMING)
        fake.set_area(3, area=2)  # meanwhile the garage arms, by a fob
        await wait_for(lambda: panel.areas[2].state == ARMED_AWAY)
        assert panel.areas[2].changed_by is None
        await wait_for(lambda: panel.areas[1].state == ARMED_AWAY)
        assert panel.areas[1].changed_by == "Home Assistant"
        fake.send_user(3)  # a code at a keypad disarms both
        fake.set_area(0, area=1)
        fake.set_area(0, area=2)
        await wait_for(lambda: panel.areas[1].state == panel.areas[2].state == DISARMED)
        assert (panel.areas[1].changed_by, panel.areas[2].changed_by) == ("User 3", "User 3")
    finally:
        await panel.stop()


async def test_ready_to_arm_is_read_again_after_a_disarm(fake):
    """It used to wait for the next regular read (up to 30 s) or a zone
    change, so a disarm from the phone left "Not ready" showing."""
    panel = await make_panel(fake)
    try:
        fake.set_area(3)
        await wait_for(lambda: panel.areas[1].state == ARMED_AWAY)
        await panel.refresh_areas()
        assert panel.areas[1].ready is False  # armed
        fake.set_area(0)  # disarmed, and nothing else happens
        await wait_for(lambda: panel.areas[1].ready is True, timeout=2)
    finally:
        await panel.stop()


async def test_a_short_keypad_message_is_read_soon_after_what_caused_it(fake, monkeypatch):
    """ "Area arm fail" shows for a few seconds: the 30 s read missed it. The
    keypad is read a moment after the panel reports something it may show,
    but not after zone changes."""
    from fake_connect_panel import run_command

    from custom_components.texecom.connect import panel as connect_panel

    monkeypatch.setattr(connect_panel, "DISPLAY_SOON_DELAY", 0.05)
    monkeypatch.setattr(connect_panel, "DISPLAY_SOON_EVERY", 0.2)
    panel = await make_panel(fake)
    try:
        run_command(fake, "display Area arm fail")
        run_command(fake, "armfail 3")
        await wait_for(lambda: panel.extra["display"] == "Area arm fail", timeout=2)
        await asyncio.sleep(0.3)
        run_command(fake, "display Area in Entry > A.")
        run_command(fake, "area entry")
        await wait_for(lambda: panel.extra["display"] == "Area in Entry > A.", timeout=2)
        await asyncio.sleep(0.3)
        reads = sum(cmd == P.CMD_GET_LCD_DISPLAY for cmd, _args in fake.commands)
        run_command(fake, "zone 2 open")
        await asyncio.sleep(0.3)
        assert sum(cmd == P.CMD_GET_LCD_DISPLAY for cmd, _args in fake.commands) == reads
    finally:
        await panel.stop()


async def test_a_drop_after_an_alarm_says_why(fake, caplog):
    """The SmartCom closes the session to report an alarm: the log says so,
    so a missing connection afterwards isn't a mystery."""
    from fake_connect_panel import run_command

    caplog.set_level("INFO", logger="custom_components.texecom")
    run_command(fake, "busy 0.3")
    fake.report_after = 0.05
    panel = await make_panel(fake)
    try:
        fake.drop_all()  # a drop with no alarm: nothing said
        await wait_for(lambda: not panel.connected)
        await wait_for(lambda: panel.connected)
        assert "to report it" not in caplog.text
        run_command(fake, "area alarm")
        await wait_for(lambda: panel.areas[1].state == TRIGGERED)
        run_command(fake, "area off")
        await wait_for(lambda: not panel.connected)
        assert "closed the connection after the alarm, probably to report it" in caplog.text
        await wait_for(lambda: panel.connected, timeout=5)
    finally:
        await panel.stop()


async def test_diagnostics_keep_the_recent_messages_and_the_unrecognised_ones(fake):
    # For panels, modules and devices we haven't seen: the user shares one file.
    panel = await make_panel(fake)
    try:
        fake.set_area(3)
        fake.send_log(250, 1, 7)  # a log type nobody has named
        fake.send_log(250, 1, 8)
        fake.send(bytes([9, 1, 2, 3]))  # a message kind nobody knows
        await wait_for(lambda: len(panel.capture.unrecognised) == 2)
        diagnostics = await panel.async_diagnostics()
        recent = [m["read"] for m in diagnostics["recent_messages"]]
        assert "area: area 1, state_code 3, state armed" in recent
        assert diagnostics["recent_messages"][-1]["raw"] == "09 01 02 03"
        unrecognised = diagnostics["unrecognised_messages"]
        assert unrecognised["log type 250, group 1"]["count"] == 2
        assert unrecognised["log type 250, group 1"]["first"]["read"].startswith("log: unknown (250)")
        assert unrecognised["unknown message 09"]["count"] == 1
        # Every kind of message, known or not, with a count and its first and last.
        kinds = diagnostics["message_kinds"]
        assert kinds["area state 3"]["count"] == 1
        assert kinds["log type 250, group 1"]["count"] == 2
        assert kinds["log type 250, group 1"]["first"] != kinds["log type 250, group 1"]["last"]
        # It keeps only so many messages, but a rare kind stays listed after it has scrolled out.
        for _ in range(capture.RECENT + 50):
            panel.capture.add(P.decode_message(bytes([P.MSG_ZONE, 1, 0])))
        assert len(panel.capture.recent) == capture.RECENT
        assert all(m["read"].startswith("zone") for m in panel.capture.recent)
        diagnostics = await panel.async_diagnostics()
        assert diagnostics["message_kinds"]["unknown message 09"]["count"] == 1
        assert diagnostics["message_kinds"]["zone bits 00"]["count"] >= capture.RECENT + 50
    finally:
        await panel.stop()


SHARED = sorted((Path(__file__).parent / "diagnostics").glob("*.json"))


@pytest.mark.parametrize("path", SHARED, ids=[p.stem for p in SHARED])
async def test_a_shared_diagnostics_file_replays_to_the_same_picture(path):
    # Each diagnostics file in tests/diagnostics (shared by a user, made anonymous)
    # becomes a simulated panel: same zones and areas, and the same messages sent again.
    shared = load_diagnostics(path)
    fake = FakeConnectPanel.from_diagnostics(shared)
    await fake.start()
    panel = await make_panel(fake)
    try:
        assert (panel.info.zones, panel.info.firmware) == (shared["info"]["zones"], shared["info"]["firmware"])
        assert [(z.number, z.name, z.panel_type, z.areas) for z in panel.zones.values()] == [
            (z["number"], z["name"], z["panel_type"], z["areas"]) for z in shared["zones"]
        ]
        assert {a.number: a.name for a in panel.areas.values()} == {a["number"]: a["name"] for a in shared["areas"]}
        sent = await fake.replay(speed=0)
        await wait_for(lambda: len(panel.capture.recent) >= sent)
        assert [m["raw"] for m in list(panel.capture.recent)[-sent:]] == [m["raw"] for m in shared["recent_messages"]]
        await asyncio.sleep(0.2)
        assert {z.number: z.state for z in panel.zones.values()} == {z["number"]: z["state"] for z in shared["zones"]}
        assert {a.number: a.state for a in panel.areas.values()} == {a["number"]: a["state"] for a in shared["areas"]}
    finally:
        await panel.stop()
        await fake.close()


@pytest.mark.parametrize(
    ("log_type", "how"),
    [(59, "engineer programming at a keypad"), (54, "a Wintex session ending"), (100, "Wintex writing settings")],
)
async def test_zones_are_read_again_after_the_panel_is_reprogrammed(fake, log_type, how, monkeypatch):
    # After any of these, a zone may have a new name, type or area: read them again.
    from custom_components.texecom.connect import events

    monkeypatch.setattr(events, "REDISCOVER_DELAY", 0.05)
    panel = await make_panel(fake)
    changed = []
    panel.on_layout_changed = lambda info, zones, areas: changed.append(zones)
    try:
        fake.zone_list = [(4, "Garage", 3, 2) if z[0] == 4 else z for z in fake.zone_list]
        fake.area_names[2] = "GARAGE"
        fake.area_states[2], fake.part_arms[2] = 0, None
        fake.send_log(log_type, 9, 3, areas=1)  # e.g. Download End, over Com Port 3
        if log_type == 100:
            fake.send_log(log_type, 9, 1, areas=1)  # one per write: read once, after the last
        await wait_for(lambda: changed)
        assert next(z for z in changed[0] if z.number == 4).areas == [2], how
        await asyncio.sleep(0.2)
        assert len(changed) == 1
    finally:
        await panel.stop()


async def test_a_refused_zone_read_never_drops_a_zone(fake, monkeypatch):
    # A busy panel (Wintex connected) can refuse a read. During a re-read that must
    # not look like a zone taken out of use: give up, and try again after reconnecting.
    from custom_components.texecom.connect import events

    monkeypatch.setattr(events, "REDISCOVER_DELAY", 0.05)
    panel = await make_panel(fake)
    changed = []
    panel.on_layout_changed = lambda info, zones, areas: changed.append(zones)
    try:
        fake.zone_list = [(4, "Garage", 3, 2) if z[0] == 4 else z for z in fake.zone_list]
        fake.area_names[2] = "GARAGE"
        fake.area_states[2], fake.part_arms[2] = 0, None
        fake.nak_next[P.CMD_GET_ZONE_DETAILS] = 1  # zone 1 refused
        fake.send_log(100, 9, 1)  # Site Data Changed
        await wait_for(lambda: panel._rediscover_pending)
        assert changed == []
        fake.drop_all()  # the next login reads them again
        await wait_for(lambda: changed, timeout=5)
        assert [(z.number, z.areas) for z in changed[0]] == [(1, [1]), (2, [1]), (3, [1]), (4, [2]), (5, [1])]
        assert not panel._rediscover_pending
    finally:
        await panel.stop()
