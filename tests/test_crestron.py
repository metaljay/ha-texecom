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
    ) -> None:
        self.elite168 = elite168
        self.login_ok = login_ok and not elite168
        self.text_error = text_error or elite168
        self.close_on_login = close_on_login
        self.udl = udl
        self.connect_arm_reply = connect_arm_reply
        self.armed = False
        self.received: list[bytes] = []
        self.connect_received: list[tuple[int, bytes]] = []  # (command, body)
        self.writers: set[asyncio.StreamWriter] = set()

    def _connect(self, writer, frame: bytes) -> None:
        sequence, command, body = frame[3], frame[4], frame[5:-1]
        self.connect_received.append((command, body))
        if command == C.CMD_LOGIN:
            reply = C.ACK if body.decode("latin1") == self.udl else C.NAK
        elif command in (C.CMD_ARM_AREA, C.CMD_DISARM_AREA):
            reply = self.connect_arm_reply
            if reply == C.ACK:
                self.armed = command == C.CMD_ARM_AREA
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
        assert fake.armed and panel.areas[1].state == ARMING
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
        assert fake.connect_received[-1] == (C.CMD_ARM_AREA, bytes([C.ARM_FULL, 0x01, 0x00]))
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
