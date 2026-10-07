"""Texecom Connect protocol: framing, CRC and message decoding.

Written for this project from the publicly released, Apache-2.0 licensed
Python implementation by Joseph Heenan (github.com/davidMbrooke/
texecom-connect), plus behaviour observed on a real Premier Elite panel.
Arm/disarm/reset and the zone state / area flag reads are adapted from
texecom2mqtt (MIT, Copyright (c) 2020 Daniel Chesterton). See NOTICE.

Frame:  't' | type | length | sequence | body... | crc8
  type    'C' command (to panel), 'R' response, 'M' unsolicited message
  length  total frame length including header and CRC
  crc8    poly 0x85, init 0xFF, not reflected, over all preceding bytes
"""

from __future__ import annotations

import re
import struct
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

START = 0x74  # 't'
TYPE_COMMAND = 0x43  # 'C'
TYPE_RESPONSE = 0x52  # 'R'
TYPE_MESSAGE = 0x4D  # 'M'

CMD_LOGIN = 1
CMD_GET_ZONE_STATE = 2
CMD_GET_ZONE_DETAILS = 3
CMD_ARM_AREA = 6
CMD_DISARM_AREA = 8
CMD_RESET_AREA = 9
CMD_GET_SYSTEM_FLAGS = 10
CMD_GET_AREA_FLAGS = 11
CMD_GET_LCD_DISPLAY = 13
CMD_GET_PANEL_IDENTIFICATION = 22
CMD_GET_DATE_TIME = 23
CMD_SET_DATE_TIME = 24
CMD_GET_SYSTEM_POWER = 25
CMD_GET_AREA_DETAILS = 35
CMD_SET_EVENT_MESSAGES = 37

ACK = 0x06
NAK = 0x15

MSG_DEBUG = 0
MSG_ZONE = 1
MSG_AREA = 2
MSG_OUTPUT = 3
MSG_USER = 4
MSG_LOG = 5

EVENT_ZONE = 1 << 1
EVENT_AREA = 1 << 2
EVENT_OUTPUT = 1 << 3
EVENT_USER = 1 << 4
EVENT_LOG = 1 << 5

ARM_FULL = 0
ARM_PART_1 = 1
ARM_PART_2 = 2
ARM_PART_3 = 3

# Indices into the GET_AREA_FLAGS response (one bitmap of areas per flag).
FLAG_ALARM = 0
FLAG_ENTRY = 17
FLAG_SECOND_ENTRY = 18
FLAG_EXIT = 19
FLAG_ARMED = 21
FLAG_FULL_ARMED = 22
FLAG_PART_ARMED = 23
FLAG_PART_ARMING = 24
FLAG_FORCE_ARMED = 26
FLAG_PART_ARM_1 = 50
FLAG_PART_ARM_2 = 51
FLAG_PART_ARM_3 = 52
AREA_FLAGS = (
    FLAG_ALARM,
    FLAG_ENTRY,
    FLAG_SECOND_ENTRY,
    FLAG_EXIT,
    FLAG_ARMED,
    FLAG_FULL_ARMED,
    FLAG_PART_ARMED,
    FLAG_PART_ARMING,
    FLAG_FORCE_ARMED,
    FLAG_PART_ARM_1,
    FLAG_PART_ARM_2,
    FLAG_PART_ARM_3,
)

AREA_STATES = ("disarmed", "in exit", "in entry", "armed", "part armed", "in alarm")
ZONE_STATES = ("secure", "active", "tamper", "short")

# Areas per panel size (zones -> areas).
AREAS_FOR_ZONES = {12: 2, 24: 2, 48: 4, 64: 4, 88: 8, 168: 16, 640: 64}


def is_nak(payload: bytes) -> bool:
    """A 1-byte NAK reply (the panel refused the command, e.g. while busy)."""
    return len(payload) == 1 and payload[0] == NAK


def crc8(data: bytes) -> int:
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x85) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def encode_frame(frame_type: int, sequence: int, body: bytes) -> bytes:
    frame = bytes([START, frame_type, len(body) + 5, sequence & 0xFF]) + body
    return frame + bytes([crc8(frame)])


def encode_command(sequence: int, command: int, body: bytes = b"") -> bytes:
    return encode_frame(TYPE_COMMAND, sequence, bytes([command]) + body)


@dataclass
class Frame:
    type: int
    sequence: int
    body: bytes


class FrameParser:
    """Turns a byte stream into frames.

    on_frame(Frame), on_drop(reason) when the panel hangs up ("+++"),
    on_error(message) for bad CRCs or skipped garbage.
    """

    def __init__(
        self,
        on_frame: Callable[[Frame], None],
        on_drop: Callable[[str], None] = lambda _r: None,
        on_error: Callable[[str], None] = lambda _m: None,
    ) -> None:
        self.on_frame = on_frame
        self.on_drop = on_drop
        self.on_error = on_error
        self.buffer = b""

    def reset(self) -> None:
        self.buffer = b""

    def push(self, chunk: bytes) -> None:
        self.buffer += chunk
        while self.buffer:
            if self.buffer[0] != START:
                # The panel signals a forced hang-up (e.g. to send an alarm
                # notification through the module) with a modem-style "+++".
                if self.buffer[:3] == b"+++":
                    self.buffer = b""
                    self.on_drop("panel dropped the connection (+++)")
                    return
                nxt = self.buffer.find(bytes([START]), 1)
                self.on_error(f"skipping {len(self.buffer) if nxt == -1 else nxt} unexpected byte(s)")
                self.buffer = b"" if nxt == -1 else self.buffer[nxt:]
                continue
            if len(self.buffer) < 4:
                return
            length = self.buffer[2]
            if length < 5:
                self.on_error(f"invalid frame length {length}")
                self.buffer = self.buffer[1:]
                continue
            if len(self.buffer) < length:
                return
            frame, self.buffer = self.buffer[:length], self.buffer[length:]
            if crc8(frame[:-1]) != frame[-1]:
                self.on_error(f"bad CRC on frame {frame.hex()}")
                continue
            self.on_frame(Frame(frame[1], frame[3], frame[4:-1]))


def area_count(panel_zones: int | None) -> int:
    return AREAS_FOR_ZONES.get(panel_zones or 0, 64)


def area_bytes(panel_zones: int | None) -> int:
    """Bytes used for an area bitmap on a panel of this size."""
    return (AREAS_FOR_ZONES.get(panel_zones or 0, 8) + 7) // 8


def area_bitmap(area: int, size: int) -> bytes:
    """Area bitmap for one area (1-based), little-endian."""
    return (1 << (area - 1)).to_bytes(size, "little")


def encode_arm(area: int, arm_type: int, panel_zones: int | None) -> bytes:
    return bytes([arm_type]) + area_bitmap(area, area_bytes(panel_zones))


def encode_disarm_or_reset(area: int, panel_zones: int | None) -> bytes:
    return area_bitmap(area, area_bytes(panel_zones))


def encode_get_zone_state(start: int, count: int, panel_zones: int | None) -> bytes:
    size = 2 if (panel_zones or 0) > 256 else 1
    return start.to_bytes(size, "little") + bytes([count])


@dataclass
class ZoneState:
    raw: int
    state: str
    fault: bool
    failed_test: bool
    alarmed: bool
    manual_bypass: bool
    auto_bypass: bool
    masked: bool

    @property
    def active(self) -> bool:
        return self.state == "active"

    @property
    def tampered(self) -> bool:
        return self.state in ("tamper", "short")

    @property
    def bypassed(self) -> bool:
        return self.manual_bypass or self.auto_bypass


def decode_zone_state(bits: int) -> ZoneState:
    """One byte per zone, same bit layout as a zone event."""
    return ZoneState(
        raw=bits,
        state=ZONE_STATES[bits & 3],
        fault=bool(bits & 4),
        failed_test=bool(bits & 8),
        alarmed=bool(bits & 16),
        manual_bypass=bool(bits & 32),
        auto_bypass=bool(bits & 64),
        masked=bool(bits & 128),
    )


def decode_area_flags(flags: bytes, areas: list[int], panel_zones: int | None) -> dict[int, tuple[str, int | None]]:
    """Each area's (state, part_arm) from a GET_AREA_FLAGS reply starting at flag 0."""
    size = area_bytes(panel_zones)

    def is_set(flag: int, area: int) -> bool:
        offset = flag * size
        if offset + size > len(flags):
            return False
        bits = int.from_bytes(flags[offset : offset + size], "little")
        return bool(bits & (1 << (area - 1)))

    result: dict[int, tuple[str, int | None]] = {}
    for area in areas:
        if is_set(FLAG_ALARM, area):
            result[area] = ("in alarm", None)
        elif is_set(FLAG_ENTRY, area) or is_set(FLAG_SECOND_ENTRY, area):
            result[area] = ("in entry", None)
        elif is_set(FLAG_EXIT, area) or is_set(FLAG_PART_ARMING, area):
            # Without this, a re-read during the exit delay would say "disarmed".
            result[area] = ("in exit", None)
        elif any(is_set(f, area) for f in (FLAG_ARMED, FLAG_FULL_ARMED, FLAG_PART_ARMED, FLAG_FORCE_ARMED)):
            part_arm = next(
                (n for n, f in ((1, FLAG_PART_ARM_1), (2, FLAG_PART_ARM_2), (3, FLAG_PART_ARM_3)) if is_set(f, area)),
                None,
            )
            result[area] = ("part armed" if part_arm else "armed", part_arm)
        else:
            result[area] = ("disarmed", None)
    return result


_DISPLAY_CLOCK = re.compile(r"\s*(\d{1,2}[:.]\d{2}([:.]\d{2})?\s+)?(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b.*$", re.IGNORECASE)


def display_message(text: str) -> str:
    """The keypad's message without its clock and date, so it only changes
    when the message does: "HOME 13:48.52 Wed 07" and "HOME Wed 07 Oct 2026"
    both become "HOME"; "System Alerts! 14:03.17 Wed 07" -> "System Alerts!"."""
    return _DISPLAY_CLOCK.sub("", text).strip() or text


def clean_text(data: bytes) -> str:
    text = data.decode("latin1").replace("\0", " ")
    text = re.sub(r"[^\x20-\x7e]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class PanelIdentification:
    text: str
    model: str
    zones: int | None
    firmware: str


def decode_panel_identification(payload: bytes) -> PanelIdentification:
    """E.g. "Elite 24     V6.05.03LS1"."""
    text = clean_text(payload)
    parts = text.split(" ")
    try:
        zones: int | None = int(parts[1])
    except (IndexError, ValueError):
        zones = None
    return PanelIdentification(
        text=text,
        model="Premier Elite" if parts[0] == "Elite" else parts[0],
        zones=zones,
        firmware=parts[-1],
    )


@dataclass
class ZoneDetails:
    type: int
    area_bitmap: int
    name: str

    @property
    def areas(self) -> list[int]:
        return [a for a in range(1, 65) if self.area_bitmap & (1 << (a - 1))]


def decode_zone_details(payload: bytes) -> ZoneDetails | None:
    size = {34: 1, 35: 2, 41: 8}.get(len(payload))
    if size is None:
        return None
    return ZoneDetails(
        type=payload[0],
        area_bitmap=int.from_bytes(payload[1 : 1 + size], "little"),
        name=clean_text(payload[1 + size :]),
    )


@dataclass
class AreaDetails:
    number: int
    name: str
    exit_delay: int
    entry1_delay: int
    entry2_delay: int
    second_entry: int


def decode_area_details(payload: bytes) -> AreaDetails | None:
    if len(payload) != 25:
        return None
    exit_delay, entry1, entry2, second = struct.unpack_from("<4H", payload, 17)
    return AreaDetails(payload[0], clean_text(payload[1:17]), exit_delay, entry1, entry2, second)


@dataclass
class SystemPower:
    panel_voltage: float
    battery_voltage: float
    panel_current: int
    battery_current: int


def decode_system_power(payload: bytes) -> SystemPower | None:
    if len(payload) != 5:
        return None
    ref, sys_v, bat_v, sys_i, bat_i = payload
    return SystemPower(
        panel_voltage=round(13.7 + (sys_v - ref) * 0.07, 2),
        battery_voltage=round(13.7 + (bat_v - ref) * 0.07, 2),
        panel_current=sys_i * 9,
        battery_current=bat_i * 9,
    )


class InvalidClock(ValueError):
    """The panel's clock holds an impossible date (e.g. after a power loss)."""


def decode_date_time(payload: bytes) -> tuple[int, int, int, int, int, int] | None:
    """(year, month, day, hours, minutes, seconds) of the panel clock; None
    for a short reply (e.g. a NAK). Raises InvalidClock for an impossible date."""
    if len(payload) < 6:
        return None
    day, month, year, hours, minutes, seconds = payload[:6]
    parts = (2000 + year, month, day, hours, minutes, seconds)
    try:
        datetime(*parts)
    except ValueError as err:
        raise InvalidClock(
            f"panel clock reads {day:02}/{month:02}/{year:02} {hours:02}:{minutes:02}:{seconds:02}"
        ) from err
    return parts


def encode_date_time(year: int, month: int, day: int, hours: int, minutes: int, seconds: int) -> bytes:
    """SET_DATE_TIME body: day, month, 2-digit year, hours, minutes, seconds."""
    return bytes([day, month, year % 100, hours, minutes, seconds])


def decode_log_timestamp(value: int) -> dict[str, int]:
    """Packed log timestamp (seconds:6, minutes:6, month:4, hours:5, day:5, year:6)."""
    return {
        "seconds": value & 63,
        "minutes": (value >> 6) & 63,
        "month": (value >> 12) & 15,
        "hours": (value >> 16) & 31,
        "day": (value >> 21) & 31,
        "year": 2000 + ((value >> 26) & 63),
    }


def decode_message(body: bytes) -> dict[str, Any]:
    """Decodes an unsolicited 'M' frame body."""
    kind = body[0] if body else -1
    p = body[1:]
    if kind == MSG_ZONE and len(p) in (2, 3):
        zone = p[0] if len(p) == 2 else p[0] | (p[1] << 8)
        return {"kind": "zone", "zone": zone, "state": decode_zone_state(p[-1])}
    if kind == MSG_AREA and len(p) >= 2:
        state = AREA_STATES[p[1]] if p[1] < len(AREA_STATES) else f"unknown ({p[1]})"
        return {"kind": "area", "area": p[0], "state_code": p[1], "state": state}
    if kind == MSG_OUTPUT and len(p) >= 2:
        return {"kind": "output", "location": p[0], "state": p[1]}
    if kind == MSG_USER and len(p) >= 2:
        methods = ("code", "tag", "code+tag")
        return {"kind": "user", "user": p[0], "method": methods[p[1]] if p[1] < 3 else f"unknown ({p[1]})"}
    if kind == MSG_LOG and len(p) in (8, 9, 10):
        if len(p) == 8:
            parameter, areas, ts = p[2], p[3], struct.unpack_from("<I", p, 4)[0]
        elif len(p) == 9:
            parameter, areas, ts = p[2], p[3] | (p[8] << 8), struct.unpack_from("<I", p, 4)[0]
        else:
            parameter, areas, ts = p[2] | (p[3] << 8), p[4] | (p[5] << 8), struct.unpack_from("<I", p, 6)[0]
        return {
            "kind": "log",
            "type": p[0],
            "group": p[1] & 0x3F,
            "comm_delayed": bool(p[1] & 0x40),
            "communicated": bool(p[1] & 0x80),
            "parameter": parameter,
            "areas": areas,
            "time": decode_log_timestamp(ts),
        }
    if kind == MSG_DEBUG:
        return {"kind": "debug", "data": p.hex()}
    return {"kind": "unknown", "data": body.hex()}


# Names of the log event types that are tampers (not zones), from the
# Apache-2.0 texecom-connect event list. Group 11 = tamper alarm, 12 = restore.
TAMPER_LOG_NAMES = {
    60: "Panel Box Tamper",
    61: "Bell Tamper",
    62: "Auxiliary Tamper",
    63: "Expander Tamper",
    64: "Keypad Tamper",
    67: "Fire Zone Tamper",
    68: "Zone Tamper",
    70: "Code Tamper Alarm",
    110: "PSU Tamper",
    121: "GSM Tamper",
}

# Log event types that are faults, with the panel's names. Whether a log
# entry starts or ends a fault comes from its group.
FAULT_LOG_NAMES = {
    47: "AC Fail",
    48: "Low Battery",
    50: "Mains Over Voltage",
    51: "Telephone Line Fault",
    52: "Fail to Communicate",
    65: "Expander Trouble",
    66: "Remote Keypad Trouble",
    96: "Expander Low Voltage",
    97: "Supervision Fault",
    99: "RF Device Low Battery",
    101: "Radio Jamming",
    104: "Zone Fault",
    105: "Zone Masked",
    107: "PSU AC Fail",
    108: "PSU Battery Fail",
    109: "PSU Low Output Fail",
    118: "Power Unit Failure",
    119: "Battery Charger Fault",
    122: "Radio Config. Failure",
}
ZONE_FAULT_LOGS = {104, 105}  # Zone Fault, Zone Masked: the zone is in `parameter`
# Groups: 1 priority alarm, 3 alarm, 9 maintenance alarm, 11 tamper, 20 fault
# start one; 2, 4, 10, 12 are the matching restores.
GROUPS_STARTING = {1, 3, 9, 11, 20}
GROUPS_RESTORING = {2, 4, 10, 12}


# ─── Names, for debug logs and diagnostics ─────────────────────────────────
# Every area flag, by number (the index into a GET_AREA_FLAGS reply), from the
# Texecom Connect protocol specification (section 4.11.5) as published in the
# texecom-connect project (Apache-2.0). The driver only relies on the flags in
# AREA_FLAGS; the rest are named in debug logs and diagnostics until they're
# confirmed on a real panel (docs/development/protocol.md).
AREA_FLAG_NAMES = (
    "Alarm",  # 0
    "Guard Alarm",  # 1
    "Guard Access Alarm",  # 2
    "Entry Alarm",  # 3
    "Confirmed Alarm",  # 4
    "24hr audible Alarm",  # 5
    "24hr Silent Alarm",  # 6
    "24hr Gas Alarm",  # 7
    "PA Alarm",  # 8
    "PA Silent Alarm",  # 9
    "Duress Alarm",  # 10
    "Fire Alarm",  # 11
    "Medical Alarm",  # 12
    "Auxiliary Alarm",  # 13
    "Tamper Alarm",  # 14
    "Abort",  # 15
    "Ready",  # 16
    "Entry",  # 17
    "Second Entry",  # 18
    "Exit",  # 19
    "Entry/Exit",  # 20
    "Armed",  # 21
    "Full Armed",  # 22
    "Part Armed",  # 23
    "Part Arming",  # 24
    "Force Armable",  # 25
    "Force Armed",  # 26
    "Arm Failed",  # 27
    "Bell SAB",  # 28
    "Bell SCB",  # 29
    "Strobe",  # 30
    "Detector Latch",  # 31
    "Detector Reset",  # 32
    "Walk Test",  # 33
    "Omitted",  # 34
    "24hr Omit",  # 35
    "Reset Required",  # 36
    "Door Strike",  # 37
    "Chime Mimic",  # 38
    "Chime Enabled",  # 39
    "Double Knock Active",  # 40
    "Beam Pair",  # 41
    "Zone on test",  # 42
    "Test Failed",  # 43
    "Internal Alarm",  # 44
    "Auto Arming",  # 45
    "Time Arming",  # 46
    "1st Code Entered",  # 47
    "2nd Code Entered",  # 48
    "Area Secured",  # 49
    "Part Arm 1",  # 50
    "Part Arm 2",  # 51
    "Part Arm 3",  # 52
    "Custom Alarm",  # 53
    "Zone Warning",  # 54
    "Arm Fail Warning",  # 55
    "Forced Entry",  # 56
    "Zones Locked Out",  # 57
    "All Armed",  # 58
    "Time Arm Disabled",  # 59
    "Armed/Alarm",  # 60
    "Intruder Alarm",  # 61
    "Speaker Mimic",  # 62
    "Full Armed/Exit",  # 63
    "Detector Fault",  # 64
    "Detector Masked",  # 65
    "Fault Present",  # 66
    "LED control",  # 67
    "Full Armed Entry",  # 68
    "Fire Sounder",  # 69
    "PA Confirmed",  # 70
    "Confirmed Intruder",  # 71
    "Seismic Alarm",  # 72
)

# Event-log types and groups, by number (texecom-connect, Apache-2.0; 204-209
# as seen on panels by michaelmarconi/texecom_alarm, MIT).
LOG_EVENT_NAMES = {
    1: "Entry/Exit 1",
    2: "Entry/Exit 2",
    3: "Interior",
    4: "Perimeter",
    5: "24hr Audible",
    6: "24hr Silent",
    7: "Audible PA",
    8: "Silent PA",
    9: "Fire Alarm",
    10: "Medical",
    11: "24Hr Gas Alarm",
    12: "Auxiliary Alarm",
    13: "24hr Tamper Alarm",
    14: "Exit Terminator",
    15: "Keyswitch - Momentary",
    16: "Keyswitch - Latching",
    17: "Security Key",
    18: "Omit Key",
    19: "Custom Alarm",
    20: "Confirmed PA Audible",
    21: "Confirmed PA Silent",
    22: "Keypad Medical",
    23: "Keypad Fire",
    24: "Keypad Audible PA",
    25: "Keypad Silent PA",
    26: "Duress Code Alarm",
    27: "Alarm Active",
    28: "Bell Active",
    29: "Re-arm",
    30: "Verified Cross Zone Alarm",
    31: "User Code",
    32: "Exit Started",
    33: "Exit Error (Arming Failed)",
    34: "Entry Started",
    35: "Part Arm Suite",
    36: "Armed with Line Fault",
    37: "Open/Close (Away Armed)",
    38: "Part Armed",
    39: "Auto Open/Close",
    40: "Auto Arm Deferred",
    41: "Open After Alarm (Alarm Abort)",
    42: "Remote Open/Close",
    43: "Quick Arm",
    44: "Recent Closing",
    45: "Reset After Alarm",
    46: "Power O/P Fault",
    47: "AC Fail",
    48: "Low Battery",
    49: "System Power Up",
    50: "Mains Over Voltage",
    51: "Telephone Line Fault",
    52: "Fail to Communicate",
    53: "Download Start",
    54: "Download End",
    55: "Log Capacity Alert (80%)",
    56: "Date Changed",
    57: "Time Changed",
    58: "Installer Programming Start",
    59: "Installer Programming End",
    60: "Panel Box Tamper",
    61: "Bell Tamper",
    62: "Auxiliary Tamper",
    63: "Expander Tamper",
    64: "Keypad Tamper",
    65: "Expander Trouble (Network error)",
    66: "Remote Keypad Trouble (Network error)",
    67: "Fire Zone Tamper",
    68: "Zone Tamper",
    69: "Keypad Lockout",
    70: "Code Tamper Alarm",
    71: "Soak Test Alarm",
    72: "Manual Test Transmission",
    73: "Automatic Test Transmission",
    74: "User Walk Test Start/End",
    75: "NVM Defaults Loaded",
    76: "First Knock",
    77: "Door Access",
    78: "Part Arm 1",
    79: "Part Arm 2",
    80: "Part Arm 3",
    81: "Auto Arming Started",
    82: "Confirmed Alarm",
    83: "Prox Tag",
    84: "Access Code Changed/Deleted",
    85: "Arm Failed",
    86: "Log Cleared",
    87: "iD Loop Shorted",
    88: "Communication Port",
    89: "TAG System Exit (Batt. OK)",
    90: "TAG System Exit (Batt. LOW)",
    91: "TAG System Entry (Batt. OK)",
    92: "TAG System Entry (Batt. LOW)",
    93: "Microphone Activated",
    94: "AV Cleared Down",
    95: "Monitored Alarm",
    96: "Expander Low Voltage",
    97: "Supervision Fault",
    98: "PA from Remote FOB",
    99: "RF Device Low Battery",
    100: "Site Data Changed",
    101: "Radio Jamming",
    102: "Test Call Passed",
    103: "Test Call Failed",
    104: "Zone Fault",
    105: "Zone Masked",
    106: "Faults Overridden",
    107: "PSU AC Fail",
    108: "PSU Battery Fail",
    109: "PSU Low Output Fail",
    110: "PSU Tamper",
    111: "Door Access",
    112: "CIE Reset",
    113: "Remote Command",
    114: "User Added",
    115: "User Deleted",
    116: "Confirmed PA",
    117: "User Acknowledged",
    118: "Power Unit Failure",
    119: "Battery Charger Fault",
    120: "Confirmed Intruder",
    121: "GSM Tamper",
    122: "Radio Config. Failure",
    204: "Quick Part Arm 1",
    205: "Quick Part Arm 2",
    206: "Quick Part Arm 3",
    207: "Remote Part Arm 1",
    208: "Remote Part Arm 2",
    209: "Remote Part Arm 3",
}
LOG_GROUP_NAMES = {
    0: "Not Reported",
    1: "Priority Alarm",
    2: "Priority Alarm Restore",
    3: "Alarm",
    4: "Restore",
    5: "Open",
    6: "Close",
    7: "Bypassed",
    8: "Unbypassed",
    9: "Maintenance Alarm",
    10: "Maintenance Restore",
    11: "Tamper Alarm",
    12: "Tamper Restore",
    13: "Test Start",
    14: "Test End",
    15: "Disarmed",
    16: "Armed",
    17: "Tested",
    18: "Started",
    19: "Ended",
    20: "Fault",
    21: "Omitted",
    22: "Reinstated",
    23: "Stopped",
    24: "Start",
    25: "Deleted",
    26: "Active",
    27: "Not Used",
    28: "Changed",
    29: "Low Battery",
    30: "Radio",
    31: "Deactivated",
    32: "Added",
    33: "Bad Action",
    34: "PA Timer Reset",
    35: "PA Zone Lockout",
}


def area_flags_set(flags: bytes, area: int, panel_zones: int | None) -> list[int]:
    """The flags set for one area in a GET_AREA_FLAGS reply that starts at flag 0."""
    size = area_bytes(panel_zones)
    return [
        flag
        for flag in range(len(flags) // size)
        if int.from_bytes(flags[flag * size : flag * size + size], "little") & (1 << (area - 1))
    ]


def flag_names(flags: list[int]) -> list[str]:
    """E.g. [16, 39] -> ["16 Ready", "39 Chime Enabled"]."""
    return [f"{f} {AREA_FLAG_NAMES[f]}" if f < len(AREA_FLAG_NAMES) else str(f) for f in flags]


def describe_log(m: dict[str, Any]) -> str:
    """A decoded log message in words, e.g. "Arm Failed (85), group Not
    Reported (0), parameter 3, areas 0x1"."""
    name = LOG_EVENT_NAMES.get(m["type"], "unknown")
    group = LOG_GROUP_NAMES.get(m["group"], "unknown")
    return f"{name} ({m['type']}), group {group} ({m['group']}), parameter {m['parameter']}, areas {m['areas']:#x}"
