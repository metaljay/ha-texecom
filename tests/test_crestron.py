"""Crestron protocol and driver tests against a scripted fake port."""

from __future__ import annotations

import asyncio

import _paths  # noqa: F401
import pytest
from test_connect import wait_for

from custom_components.texecom.crestron import protocol as P
from custom_components.texecom.crestron.panel import CrestronPanel
from custom_components.texecom.panel import (
    ARMED_AWAY,
    ARMED_NIGHT,
    ARMING,
    DISARMED,
    TRIGGERED,
    PanelArea,
    PanelError,
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
    inside a UDL session get ERROR (seen when the login gets no OK)."""

    def __init__(self, login_ok: bool = True, text_error: bool = False) -> None:
        self.login_ok = login_ok
        self.text_error = text_error
        self.armed = False
        self.received: list[bytes] = []
        self.writers: set[asyncio.StreamWriter] = set()

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
                            if self.login_ok:
                                writer.write(b"OK\r\n")
                        else:
                            writer.write(b"ERROR\r\n" if self.text_error else b"OK\r\n")
                    elif 3 <= buf[0] < 0x20 and len(buf) >= buf[0]:
                        frame, buf = buf[: buf[0]], buf[buf[0] :]
                        self.received.append(frame)
                        if frame[1] != 0x48:  # no reply to logout
                            writer.write(P.wintex_frame(0x06))
                    else:
                        break
        finally:
            self.writers.discard(writer)


async def make_panel(port: int, **kwargs) -> CrestronPanel:
    opts = {
        "part_arms": {"night": 1, "home": 0},
        "udl": "1234",
        "status_poll": 0,
        "reconnect_min": 0.05,
        "blackout": 0.1,
    }
    opts.update(kwargs)
    panel = CrestronPanel(
        zones=[PanelZone(n, f"Zone {n}") for n in range(1, 6)],
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
