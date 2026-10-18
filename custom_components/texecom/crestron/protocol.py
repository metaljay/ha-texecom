"""Texecom Crestron (simple) protocol helpers, plus the binary UDL frames used
for part arms and as a fallback when the panel answers a text command ERROR.

Line formats (each starts with a double quote), confirmed on a real Premier
Elite over a serial-to-IP bridge:
  "Z0071   zone 007, status 1 (active) / 0 (clear)
  "A0013   area 001 armed by user 3 (the user number is variable width)
  "D0013   area 001 disarmed by user 3
  "L0010   area 001 in alarm
  "X0010   area 001 exit delay started
  "E0010   area 001 entry delay started
  "U0030   user 003 entered a code at a keypad
  "NY      ASTATUS reply: one letter per area (Y armed / N not armed)
  OK / ERROR   command acknowledgement

Inside a UDL session some panels (a Premier Elite 168, V6.06.03) refuse the
text and binary UDL arm/disarm commands but answer Texecom Connect frames
('t' ...), so those replies are pulled out of the stream too.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from ..connect import protocol as C

MAX_COMMAND_AREA = 8  # highest area the single-byte bitmask can address

WINTEX_ACK = 0x06
WINTEX_NAK = 0x0F

# A partial frame is waited for only up to this many bytes, so stray bytes
# that look like a frame's start can't hold up the text lines behind them.
MAX_PENDING_FRAME = 64  # the replies inside a session are a few bytes

_AREA_RE = re.compile(r'^"([ADLXE])(\d{3})(\d*)')
_USER_RE = re.compile(r'^"U(\d{3})')
_ASTATUS_RE = re.compile(r'^"([YN]+)$')


def parse_line(raw: str) -> dict[str, Any]:
    line = raw.strip()
    if m := _ASTATUS_RE.match(line):
        return {"type": "astatus", "armed": [c == "Y" for c in m.group(1)]}
    if line == "OK":
        return {"type": "ok"}
    if line == "ERROR":
        return {"type": "error"}
    if line.startswith('"Z') and len(line) >= 6 and line[2:5].isdigit():
        return {"type": "zone", "zone": int(line[2:5]), "status": line[5]}
    if m := _AREA_RE.match(line):
        return {"type": "area", "event": m.group(1), "area": int(m.group(2)), "user": m.group(3)}
    if m := _USER_RE.match(line):
        return {"type": "user", "user": int(m.group(1))}
    return {"type": "unknown", "line": line}


def wintex_frame(command: int, payload: list[int] | None = None) -> bytes:
    """A binary UDL frame: [length][command][payload...][checksum], where the
    checksum makes the byte sum 0xFF."""
    data = [len(payload or []) + 3, command, *(payload or [])]
    return bytes([*data, (0xFF - sum(data)) & 0xFF])


# Wintex logout ('H'): ends the UDL session; the text feed resumes ~30 s later.
WINTEX_LOGOUT = wintex_frame(0x48)


def strip_wintex_frames(data: bytes, on_frame: Callable[[bytes], None]) -> bytes:
    """After a UDL login the port replies with binary frames that have no line
    terminator: remove complete frames from the start of the buffer. Text
    lines start with a printable character, frames with their length byte."""
    while len(data) >= 3:
        length = data[0]
        if length < 3 or length >= 0x20 or len(data) < length:
            break
        if sum(data[:length]) & 0xFF != 0xFF:
            break
        on_frame(data[:length])
        data = data[length:]
    return data


def strip_connect_frames(data: bytes, on_connect_frame: Callable[[C.Frame], None]) -> bytes:
    """Remove complete Texecom Connect replies and messages ('t', type, total
    length, sequence, body, CRC) from the start of the buffer. Text lines
    never start with 't', and UDL frames start with a length byte below 0x20."""
    while len(data) >= 3 and data[0] == C.START and data[1] in (C.TYPE_RESPONSE, C.TYPE_MESSAGE):
        length = data[2]
        if length < 5 or len(data) < length:
            break
        frame = data[:length]
        if C.crc8(frame[:-1]) != frame[-1]:
            break
        on_connect_frame(C.Frame(frame[1], frame[3], frame[4:-1]))
        data = data[length:]
    return data


def connect_command(sequence: int, command: int, body: bytes = b"") -> bytes:
    """A Texecom Connect command frame, sent inside a UDL session."""
    return C.encode_command(sequence, command, body)


def part_arm_frame(area: int, part_arm: int) -> bytes:
    """Binary UDL part arm ('S' <area index> <part arm>). Unlike Crestron \\Y
    (always Part Arm 1) it reaches Part Arm 1-3. Confirmed for area 1 on an
    Elite 24 V6.05.03 (layout from ricol99/casa and shuckc/pytexalarm); other
    areas are unverified, so only area 1 is allowed."""
    if area != 1 or part_arm not in (1, 2, 3):
        raise ValueError(f"Part arm {part_arm} of area {area} isn't supported over Crestron (area 1 only)")
    return wintex_frame(0x53, [area - 1, part_arm])


def udl_frame_for(letter: str, area: int) -> bytes | None:
    """Binary UDL equivalent of a Crestron A/Y/D command for area 1, else None."""
    if area != 1:
        return None
    if letter == "A":
        return wintex_frame(0x41, [0])
    if letter == "D":
        return wintex_frame(0x44, [0])
    if letter == "Y":
        return part_arm_frame(1, 1)
    return None


def area_bitmask(area: int) -> str:
    """Area 1 -> 0x01, area 2 -> 0x02 ... area 8 -> 0x80."""
    if not 1 <= area <= MAX_COMMAND_AREA:
        raise ValueError(f"Area {area} can't be armed over Crestron (1-{MAX_COMMAND_AREA} only)")
    return chr(1 << (area - 1))


def encode_command(command: str) -> bytes:
    """E.g. \\W1234/ or \\A<bitmask>/, latin1 so bitmask bytes stay one byte."""
    return f"\\{command}/".encode("latin1")


class LineSplitter:
    """Buffers a byte stream into lines, pulling out binary UDL frames and
    Texecom Connect frames."""

    def __init__(
        self,
        on_line: Callable[[str], None],
        on_frame: Callable[[bytes], None],
        on_connect_frame: Callable[[C.Frame], None] = lambda _f: None,
    ) -> None:
        self.on_line = on_line
        self.on_frame = on_frame
        self.on_connect_frame = on_connect_frame
        self.buffer = b""

    def reset(self) -> None:
        self.buffer = b""

    def _strip_frames(self, data: bytes) -> bytes:
        # Either kind can follow the other (e.g. a UDL NAK then a Connect ACK).
        while True:
            stripped = strip_connect_frames(strip_wintex_frames(data, self.on_frame), self.on_connect_frame)
            if stripped == data:
                return data
            data = stripped

    @staticmethod
    def _frame_pending(data: bytes) -> bool:
        """The start of a frame whose rest hasn't arrived yet. Its bytes can
        include CR or LF, so it mustn't be cut up as a line meanwhile."""
        if not data or len(data) >= MAX_PENDING_FRAME:
            return False
        if data[0] == C.START:
            return len(data) < 2 or (
                data[1] in (C.TYPE_RESPONSE, C.TYPE_MESSAGE) and (len(data) < 3 or len(data) < data[2])
            )
        return 3 <= data[0] < 0x20 and len(data) < data[0]

    def push(self, chunk: bytes) -> None:
        # Frames and lines can share one read (e.g. a NAK, an ACK and then a
        # line), so take whatever comes first, one at a time.
        self.buffer += chunk
        while True:
            self.buffer = self._strip_frames(self.buffer)
            if self._frame_pending(self.buffer):
                return
            end = re.search(rb"\r?\n", self.buffer)
            if not end:
                return
            line, self.buffer = self.buffer[: end.start()], self.buffer[end.end() :]
            text = line.decode("latin1")
            if text.strip():
                self.on_line(text)
