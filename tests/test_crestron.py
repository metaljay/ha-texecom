"""Crestron protocol and driver tests against a scripted fake port."""

from __future__ import annotations

import asyncio
import socket

import _paths  # noqa: F401
import pytest
from test_connect import wait_for

from custom_components.texecom.connect import protocol as C
from custom_components.texecom.crestron import protocol as P
from custom_components.texecom.crestron.panel import CrestronPanel
from custom_components.texecom.panel import (
    ARMED_AWAY,
    ARMED_NIGHT,
    ARMING,
    DISARMED,
    PENDING,
    TRIGGERED,
    PanelArea,
    PanelError,
    PanelNotConnected,
    PanelZone,
)


def test_parse_line():
    assert P.parse_line('"Z0071') == {"type": "zone", "zone": 7, "status": "1"}
    assert P.parse_line('"A00112') == {"type": "area", "event": "A", "area": 1, "user": "12"}
    assert P.parse_line('"NY') == {"type": "astatus", "armed": [False, True]}
    assert P.parse_line("ERROR") == {"type": "error"}
    assert P.parse_line('"U0030') == {"type": "user", "user": 3}


def test_wintex_frames_checksum_and_splitting():
    frame = P.part_arm_frame(1, 2)
    assert frame == bytes([5, 0x53, 0, 2, 0xFF - (5 + 0x53 + 2)])
    assert sum(frame) & 0xFF == 0xFF
    lines, frames = [], []
    splitter = P.LineSplitter(lines.append, frames.append)
    ack = P.wintex_frame(0x06)
    splitter.push(b'"Z0011\r\nOK\r\n' + ack)
    assert lines == ['"Z0011', "OK"] and frames == [ack]
    with pytest.raises(ValueError):
        P.part_arm_frame(2, 1)


class FakeCrestronPort:
    """Answers like a panel's Crestron port. text_error makes text commands
    inside a UDL session get ERROR (seen when the login gets no OK).

    elite168 answers as a Premier Elite 168 (V6.06.03) did: no OK to the
    login, ERROR to text commands, NAK to the binary UDL arm/disarm frames,
    ACK to the logout, and Texecom Connect frames answered inside the
    session (login with the UDL code, then arm and disarm)."""

    def __init__(
        self,
        login_ok: bool = True,
        text_error: bool = False,
        close_on_login: bool = False,
        elite168: bool = False,
        udl: str = "1234",
        connect_arm_reply: int = C.ACK,
        arm_fails: bool = False,
    ) -> None:
        self.elite168 = elite168
        self.arm_fails = arm_fails  # a door left open: the arm logs Exit Error and Arm Failed
        # The stored event log (a 168's 9-byte entries in a 2000-entry ring).
        self.log: dict[int, bytes] = {}
        self.log_pointer = 0
        self.login_ok = login_ok and not elite168
        self.text_error = text_error or elite168
        self.close_on_login = close_on_login
        self.udl = udl
        self.connect_arm_reply = connect_arm_reply
        self.armed = False
        self.received: list[bytes] = []
        self.connect_received: list[tuple[int, bytes]] = []  # (command, body)
        self.writers: set[asyncio.StreamWriter] = set()

    def add_log(self, log_type: int, parameter: int = 0, group: int = 0, areas: int = 1) -> None:
        stamp = 35 | 11 << 6 | 10 << 12 | 13 << 16 | 18 << 21 | 26 << 26  # 13:11:35 18/10/2026
        entry = bytes([log_type, group, parameter, areas & 0xFF]) + stamp.to_bytes(4, "little") + bytes([areas >> 8])
        self.log[self.log_pointer] = entry
        self.log_pointer = (self.log_pointer + 1) % 2000

    def _exit_delay_ended(self) -> None:
        if self.arm_fails:
            self.add_log(33)  # Exit Error (Arming Failed)
            self.add_log(85, parameter=94)  # Arm Failed: zone 94 active
        else:
            self.armed = True
            self.add_log(113, parameter=1, group=9)  # Remote Command
            self.add_log(208, group=6)  # Remote Part Arm 2

    def _connect(self, writer, frame: bytes) -> None:
        sequence, command, body = frame[3], frame[4], frame[5:-1]
        self.connect_received.append((command, body))
        if command == C.CMD_LOGIN:
            reply = C.ACK if body.decode("latin1") == self.udl else C.NAK
        elif command == C.CMD_GET_SYSTEM_POWER:
            # reference, panel volts, battery volts, panel current, battery current:
            # 13.63 V, 13.63 V, 297 mA, 18 mA (as read on mains)
            writer.write(C.encode_frame(C.TYPE_RESPONSE, sequence, bytes([command, 100, 99, 99, 33, 2])))
            return
        elif command == C.CMD_GET_LOG_POINTER:
            pointer = self.log_pointer.to_bytes(2, "little")
            writer.write(C.encode_frame(C.TYPE_RESPONSE, sequence, bytes([command]) + pointer))
            return
        elif command == C.CMD_GET_LOG_EVENTS:
            index, count = int.from_bytes(body[:2], "little"), body[2]
            if index + count > 2000 or any((index + n) not in self.log for n in range(count)):
                reply = C.NAK
            else:
                data = b"".join(self.log[index + n] for n in range(count))
                writer.write(C.encode_frame(C.TYPE_RESPONSE, sequence, bytes([command]) + data))
                return
        elif command in (C.CMD_ARM_AREA, C.CMD_DISARM_AREA):
            reply = self.connect_arm_reply
            if reply == C.ACK:
                if command == C.CMD_DISARM_AREA:
                    self.armed = False
                    self.add_log(42)  # Remote Open/Close
                else:
                    self.add_log(32)  # Exit Started
                    # The rest is logged when the (short, simulated) exit delay ends.
                    asyncio.get_running_loop().call_later(0.05, self._exit_delay_ended)
        else:
            reply = C.NAK
        writer.write(C.encode_frame(C.TYPE_RESPONSE, sequence, bytes([command, reply])))

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        for w in list(self.writers):
            w.close()
        self.server.close()

    def send(self, line: str) -> None:
        for w in list(self.writers):
            w.write(f"{line}\r\n".encode())

    async def _client(self, reader, writer) -> None:
        self.writers.add(writer)
        buf = b""
        try:
            while data := await reader.read(256):
                buf += data
                while buf:
                    if buf.startswith(b"ASTATUS\r\n"):
                        buf = buf[9:]
                        self.received.append(b"ASTATUS")
                        writer.write(b'"Y\r\n' if self.armed else b'"N\r\n')
                    elif buf.startswith(b"\\") and b"/" in buf:
                        end = buf.index(b"/") + 1
                        cmd, buf = buf[1 : end - 1], buf[end:]
                        self.received.append(cmd)
                        if cmd.startswith(b"W"):
                            if self.close_on_login:
                                writer.close()
                                return
                            if self.login_ok:
                                writer.write(b"OK\r\n")
                        else:
                            writer.write(b"ERROR\r\n" if self.text_error else b"OK\r\n")
                    elif buf[0] == C.START and len(buf) >= 3 and len(buf) >= buf[2]:
                        frame, buf = buf[: buf[2]], buf[buf[2] :]
                        self.received.append(frame)
                        self._connect(writer, frame)
                    elif 3 <= buf[0] < 0x20 and len(buf) >= buf[0]:
                        frame, buf = buf[: buf[0]], buf[buf[0] :]
                        self.received.append(frame)
                        if self.elite168:
                            # The real panel ACKs the logout and NAKs the arm/disarm frames.
                            writer.write(P.wintex_frame(0x06 if frame[1] == 0x48 else 0x0F))
                        elif frame[1] != 0x48:  # no reply to logout
                            writer.write(P.wintex_frame(0x06))
                    else:
                        break
        finally:
            self.writers.discard(writer)
            writer.close()  # its side too, or asyncio warns "unclosed StreamWriter"


async def make_panel(port: int, **kwargs) -> CrestronPanel:
    opts = {
        "part_arms": {"night": 1, "home": 0},
        "udl": "1234",
        "status_poll": 0,
        "reconnect_min": 0.05,
        "blackout": 0.1,
        "login_timeout": 0.3,
        "zones": [PanelZone(n, f"Zone {n}") for n in range(1, 6)],
    }
    opts.update(kwargs)
    panel = CrestronPanel(
        areas=[PanelArea(1, "Area A")],
        host="127.0.0.1",
        port=port,
        **opts,
    )
    await panel.start()
    await wait_for(lambda: panel.connected and panel.areas[1].known)
    return panel


@pytest.fixture
async def port():
    fake = FakeCrestronPort()
    yield fake, await fake.start()
    await fake.close()


async def test_events_drive_zone_and_area_state(port):
    fake, number = port
    panel = await make_panel(number, event_coalesce=0)
    try:
        assert panel.areas[1].state == DISARMED
        fake.send('"Z0031')
        await wait_for(lambda: panel.zones[3].active)
        fake.send('"X0010')
        await wait_for(lambda: panel.areas[1].state == ARMING)
        fake.send('"A0013')
        await wait_for(lambda: panel.areas[1].state == ARMED_AWAY)
        assert panel.areas[1].changed_by == "User 3"
        fake.send('"L0010')
        await wait_for(lambda: panel.areas[1].state == TRIGGERED)
        fake.send('"D0013')
        await wait_for(lambda: panel.areas[1].state == DISARMED)
    finally:
        await panel.stop()


async def test_an_alarm_stays_an_alarm_when_the_panel_goes_back_to_entry(port):
    fake, number = port
    panel = await make_panel(number, event_coalesce=0)
    try:
        fake.send('"A0013')
        fake.send('"E0010')
        await wait_for(lambda: panel.areas[1].state == PENDING)
        fake.send('"L0010')
        await wait_for(lambda: panel.areas[1].state == TRIGGERED)
        fake.send('"E0010')  # the entry zone seen again, sirens still sounding
        fake.send('"Z0011')
        await wait_for(lambda: panel.zones[1].active)
        assert panel.areas[1].state == TRIGGERED
        fake.send('"D0013')
        await wait_for(lambda: panel.areas[1].state == DISARMED)
    finally:
        await panel.stop()


async def test_held_back_burst_doesnt_flash_armed(port):
    fake, number = port
    seen = []
    panel = await make_panel(number, event_coalesce=0.1)
    panel.add_listener(lambda: seen.append(panel.areas[1].state))
    try:
        fake.send('"X0010\r\n"A0010\r\n"D0010')
        await asyncio.sleep(0.3)
        assert ARMED_AWAY not in seen and panel.areas[1].state == DISARMED
    finally:
        await panel.stop()


async def test_part_arm_uses_binary_frame_inside_udl_session(port):
    fake, number = port
    panel = await make_panel(number)
    try:
        await panel.arm(1, "night")
        await wait_for(lambda: fake.received[-1] == P.WINTEX_LOGOUT)
        assert fake.received[-3:] == [b"W1234", P.part_arm_frame(1, 1), P.WINTEX_LOGOUT]
        assert panel.areas[1].state == ARMING
        fake.send('"A0010')  # the held-back event arrives later
        await wait_for(lambda: panel.areas[1].state == ARMED_NIGHT)
        assert panel.areas[1].changed_by == "Home Assistant"  # not "User 0"
    finally:
        await panel.stop()


async def test_text_error_falls_back_to_binary():
    fake = FakeCrestronPort(login_ok=False, text_error=True)
    number = await fake.start()
    panel = await make_panel(number)
    try:
        await asyncio.wait_for(panel.disarm(1), 15)
        assert P.udl_frame_for("D", 1) in fake.received
        assert panel.areas[1].state == DISARMED
    finally:
        await panel.stop()
        await fake.close()


def test_splitter_pulls_out_connect_replies():
    """Inside a UDL session an Elite 168 answers Texecom Connect frames on the
    Crestron port; they arrive between text lines and binary UDL frames."""
    lines, frames, connect = [], [], []
    splitter = P.LineSplitter(lines.append, frames.append, connect.append)
    nak = P.wintex_frame(0x0F)
    reply = C.encode_frame(C.TYPE_RESPONSE, 7, bytes([C.CMD_LOGIN, C.ACK]))
    assert reply == bytes.fromhex("745207070106") + reply[-1:]
    stream = b"ERROR\r\n" + nak + reply + b'"Z0011\r\n'
    for i in range(len(stream)):  # however the bytes are split up
        splitter.push(stream[i : i + 1])
    assert lines == ["ERROR", '"Z0011']
    assert frames == [nak]
    assert [(f.type, f.sequence, f.body) for f in connect] == [(C.TYPE_RESPONSE, 7, bytes([C.CMD_LOGIN, C.ACK]))]


async def test_refused_disarm_falls_back_to_connect_inside_the_session():
    fake = FakeCrestronPort(elite168=True)
    number = await fake.start()
    panel = await make_panel(number)
    try:
        await asyncio.wait_for(panel.disarm(1), 20)
        assert b"W1234" in fake.received and P.udl_frame_for("D", 1) in fake.received
        # 5 zones -> an 8-area panel -> 1-byte area bitmap
        assert fake.connect_received == [(C.CMD_LOGIN, b"1234"), (C.CMD_DISARM_AREA, b"\x01")]
        await wait_for(lambda: fake.received[-1] == P.WINTEX_LOGOUT)
        assert panel.areas[1].state == DISARMED
    finally:
        await panel.stop()
        await fake.close()


async def test_refused_part_arm_falls_back_to_connect_with_its_part_arm():
    fake = FakeCrestronPort(elite168=True)
    number = await fake.start()
    panel = await make_panel(number, part_arms={"night": 2, "home": 0})
    try:
        await asyncio.wait_for(panel.arm(1, "night"), 20)
        assert P.part_arm_frame(1, 2) in fake.received
        assert fake.connect_received[-1] == (C.CMD_ARM_AREA, bytes([C.ARM_PART_2, 0x01]))
        assert panel.areas[1].state == ARMING
        await wait_for(lambda: fake.armed)  # once the exit delay ends
        fake.send('"A0010')
        await wait_for(lambda: panel.areas[1].state == ARMED_NIGHT)
    finally:
        await panel.stop()
        await fake.close()


async def test_connect_fallback_uses_the_panels_area_bitmap_size():
    """A 168-zone panel has 16 areas, so Connect area bitmaps are 2 bytes."""
    fake = FakeCrestronPort(elite168=True)
    number = await fake.start()
    panel = await make_panel(number, zones=[PanelZone(n, f"Zone {n}") for n in range(1, 169)])
    try:
        await asyncio.wait_for(panel.arm(1, "away"), 20)
        assert (C.CMD_ARM_AREA, bytes([C.ARM_FULL, 0x01, 0x00])) in fake.connect_received
    finally:
        await panel.stop()
        await fake.close()


async def test_connect_fallback_refused_is_an_error_and_still_logs_out():
    fake = FakeCrestronPort(elite168=True, connect_arm_reply=C.NAK)
    number = await fake.start()
    panel = await make_panel(number)
    try:
        with pytest.raises(PanelError, match="refused"):
            await asyncio.wait_for(panel.disarm(1), 20)
        await wait_for(lambda: fake.received[-1] == P.WINTEX_LOGOUT)
    finally:
        await panel.stop()
        await fake.close()


async def test_connect_login_refused_says_so():
    fake = FakeCrestronPort(elite168=True, udl="9999")
    number = await fake.start()
    panel = await make_panel(number)
    try:
        with pytest.raises(PanelError, match="UDL code"):
            await asyncio.wait_for(panel.disarm(1), 20)
        assert [c for c, _ in fake.connect_received] == [C.CMD_LOGIN]  # no command after a refused login
        await wait_for(lambda: fake.received[-1] == P.WINTEX_LOGOUT)
    finally:
        await panel.stop()
        await fake.close()


async def test_power_is_read_inside_a_short_udl_session():
    fake = FakeCrestronPort(elite168=True)
    number = await fake.start()
    panel = await make_panel(number, power_poll=0.3, power_first_delay=0.05)
    seen = []
    panel.add_listener(lambda: seen.append(panel.extra.get("power")))
    try:
        await wait_for(lambda: "power" in panel.extra)
        power = panel.extra["power"]
        assert (power.panel_voltage, power.battery_voltage, power.panel_current, power.battery_current) == (
            13.63,
            13.63,
            297,
            18,
        )
        assert any(seen)  # entities were told
        assert fake.connect_received[:2] == [(C.CMD_LOGIN, b"1234"), (C.CMD_GET_SYSTEM_POWER, b"")]
        await wait_for(lambda: fake.received[-1] == P.WINTEX_LOGOUT)
        await wait_for(lambda: [c for c, _ in fake.connect_received].count(C.CMD_GET_SYSTEM_POWER) >= 2)  # repeats
    finally:
        await panel.stop()
        await fake.close()


async def test_power_isnt_read_without_a_udl_code_or_when_switched_off():
    fake = FakeCrestronPort(elite168=True)
    number = await fake.start()
    for kwargs in ({"udl": None}, {"power_poll": 0}):
        panel = await make_panel(number, power_first_delay=0.05, **{"power_poll": 0.1, **kwargs})
        try:
            await asyncio.sleep(0.5)
            assert "power" not in panel.extra and not fake.connect_received
        finally:
            await panel.stop()
    await fake.close()


async def test_power_isnt_read_while_arming_entering_or_in_alarm():
    """A session while someone may be at the keypad (exit, entry, alarm) could
    get in their way, so the read waits for a settled state."""
    fake = FakeCrestronPort(elite168=True)
    number = await fake.start()
    panel = await make_panel(number, power_poll=0.1, power_first_delay=0.05, power_retry=0.1)
    try:
        for event in ("X", "E", "L"):
            fake.send(f'"{event}0010')
            await wait_for(lambda: panel.areas[1].state != DISARMED)
            await wait_for(lambda: not panel._busy)  # a read already under way may finish
            fake.connect_received.clear()
            await asyncio.sleep(0.5)
            assert not fake.connect_received, event
            fake.send('"D0010')
            await wait_for(lambda: panel.areas[1].state == DISARMED)
        await wait_for(lambda: "power" in panel.extra)  # settled again: read
    finally:
        await panel.stop()
        await fake.close()


async def test_a_refused_power_read_is_quiet_and_keeps_trying():
    fake = FakeCrestronPort(elite168=True, udl="9999")  # the panel refuses our code
    number = await fake.start()
    panel = await make_panel(number, power_poll=0.1, power_first_delay=0.05)
    try:
        await wait_for(lambda: [c for c, _ in fake.connect_received].count(C.CMD_LOGIN) >= 2)
        assert "power" not in panel.extra and panel.connected
    finally:
        await panel.stop()
        await fake.close()


async def test_astatus_corrects_missed_events_but_keeps_triggered(port):
    fake, number = port
    panel = await make_panel(number, status_poll=0.1, keypad_arm_mode="night")
    try:
        fake.armed = True
        await wait_for(lambda: panel.areas[1].state == ARMED_NIGHT)
        fake.armed = False
        fake.send('"L0010')
        await wait_for(lambda: panel.areas[1].state == TRIGGERED)
        await asyncio.sleep(0.3)  # several "not armed" polls
        assert panel.areas[1].state == TRIGGERED
    finally:
        await panel.stop()


async def test_arming_without_udl_is_refused(port):
    _fake, number = port
    panel = await make_panel(number, udl=None)
    try:
        with pytest.raises(PanelError):
            await panel.arm(1, "away")
    finally:
        await panel.stop()


async def test_a_bridge_thats_down_gives_one_warning_not_one_per_attempt(caplog):
    caplog.set_level("DEBUG", logger="custom_components.texecom")
    with socket.socket() as s:  # a port nothing listens on
        s.bind(("127.0.0.1", 0))
        number = s.getsockname()[1]
    panel = CrestronPanel(
        part_arms={"night": 1, "home": 0},
        zones=[],
        areas=[PanelArea(1, "Area A")],
        udl=None,
        host="127.0.0.1",
        port=number,
        status_poll=0,
        reconnect_min=0.01,
    )
    await panel.start()
    try:
        await wait_for(lambda: sum("cannot open" in r.getMessage() for r in caplog.records) >= 6)
        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert len(warnings) == 1 and "still trying" in warnings[0]
    finally:
        await panel.stop()


async def test_the_connection_closing_during_a_command_says_not_connected():
    """Not "the panel didn't accept the request": the link dropped, and it
    reconnects by itself."""
    fake = FakeCrestronPort(close_on_login=True)
    number = await fake.start()
    panel = await make_panel(number)
    try:
        with pytest.raises(PanelNotConnected):
            await panel.arm(1, "away")
    finally:
        await panel.stop()
        await fake.close()


# ─── The stored event log (through Texecom Connect) ─────────────────────────

ZONES_168 = [PanelZone(n, f"Zone {n}") for n in range(1, 169)]


async def make_log_panel(fake, **kwargs):
    events: list[tuple[str, dict]] = []
    number = await fake.start()
    opts = {
        "zones": ZONES_168,
        "power_poll": 0,
        "after_arm_check": 0.1,
        "on_event": lambda t, d: events.append((t, d)),
    }
    panel = await make_panel(number, **{**opts, **kwargs})
    return panel, events


def logs(events):
    return [(d["index"], d["log_type"]) for t, d in events if t == "log"]


async def test_a_failed_arm_is_read_from_the_log_and_reported():
    fake = FakeCrestronPort(elite168=True, arm_fails=True)
    fake.add_log(42)  # older entries are never reported
    panel, events = await make_log_panel(fake, part_arms={"night": 2, "home": 0})
    try:
        await asyncio.wait_for(panel.arm(1, "night"), 20)
        assert panel.areas[1].state == ARMING
        await wait_for(lambda: any(t == "arm_failed" for t, _ in events))
        failed = next(d for t, d in events if t == "arm_failed")
        assert failed == {"areas": 1, "zone": 94, "zone_name": "Zone 94"}
        assert [n for _i, n in logs(events)] == [33, 85]  # read from where the log ended at the arm
        log = next(d for t, d in events if t == "log" and d["log_type"] == 85)
        assert log["name"] == "Arm Failed" and log["panel_time"] == "2026-10-18T13:11:35"
        await wait_for(lambda: panel.areas[1].state == DISARMED)
    finally:
        await panel.stop()
        await fake.close()


async def test_a_good_arm_is_logged_but_not_reported_as_failed():
    fake = FakeCrestronPort(elite168=True)
    panel, events = await make_log_panel(fake, part_arms={"night": 2, "home": 0})
    try:
        await asyncio.wait_for(panel.arm(1, "night"), 20)
        await wait_for(lambda: len(logs(events)) == 2)
        assert [n for _i, n in logs(events)] == [113, 208]
        assert not any(t == "arm_failed" for t, _ in events) and panel.areas[1].state == ARMING
    finally:
        await panel.stop()
        await fake.close()


async def test_the_regular_reading_catches_up_with_the_log():
    fake = FakeCrestronPort(elite168=True)
    for _ in range(3):
        fake.add_log(42)
    panel, events = await make_log_panel(fake, power_poll=0.2, power_first_delay=0.05)
    try:
        await wait_for(lambda: panel._log_pointer == 3)  # the first reading only notes where the log ends
        assert not logs(events)
        fake.add_log(32)
        fake.add_log(42)
        await wait_for(lambda: len(logs(events)) == 2)
        assert logs(events) == [(3, 32), (4, 42)]
    finally:
        await panel.stop()
        await fake.close()


async def test_the_log_is_read_across_the_end_of_the_ring():
    fake = FakeCrestronPort(elite168=True)
    fake.log_pointer = 1997
    fake.add_log(42)  # index 1997
    panel, events = await make_log_panel(fake, power_poll=0.2, power_first_delay=0.05)
    try:
        await wait_for(lambda: panel._log_pointer == 1998)
        for t in (32, 33, 85, 42):  # 1998, 1999, 0, 1
            fake.add_log(t)
        await wait_for(lambda: len(logs(events)) == 4)
        assert logs(events) == [(1998, 32), (1999, 33), (0, 85), (1, 42)]
    finally:
        await panel.stop()
        await fake.close()


async def test_after_a_long_gap_only_the_latest_entries_are_read():
    fake = FakeCrestronPort(elite168=True)
    panel, events = await make_log_panel(fake, power_poll=0.3, power_first_delay=0.05)
    try:
        await wait_for(lambda: panel._log_pointer == 0)
        for _ in range(40):
            fake.add_log(42)
        await wait_for(lambda: len(logs(events)) == 30)
        assert [i for i, _ in logs(events)] == list(range(10, 40))
    finally:
        await panel.stop()
        await fake.close()
