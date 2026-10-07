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
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

MAX_COMMAND_AREA = 8  # highest area the single-byte bitmask can address

WINTEX_ACK = 0x06
WINTEX_NAK = 0x0F

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
    """Buffers a byte stream into lines, pulling out binary UDL frames."""

    def __init__(self, on_line: Callable[[str], None], on_frame: Callable[[bytes], None]) -> None:
        self.on_line = on_line
        self.on_frame = on_frame
        self.buffer = b""

    def reset(self) -> None:
        self.buffer = b""

    def push(self, chunk: bytes) -> None:
        self.buffer = strip_wintex_frames(self.buffer + chunk, self.on_frame)
        *lines, self.buffer = re.split(rb"\r?\n", self.buffer)
        for line in lines:
            text = line.decode("latin1")
            if text.strip():
                self.on_line(text)
        # A frame straight after a line (e.g. "OK\r\n" + ACK) shouldn't wait
        # for more data to arrive.
        self.buffer = strip_wintex_frames(self.buffer, self.on_frame)
