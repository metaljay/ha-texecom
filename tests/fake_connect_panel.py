"""Fake Texecom Connect panel (Premier Elite 24, one area) for tests and demos.

    python3 tests/fake_connect_panel.py [port] [--demo] [--clock-reset] [--commands]

--demo uses friendlier zone names (for screenshots) and wanders a few zones
between active and secure. --clock-reset starts its clock at 31 Oct 2023.
--commands reads commands from standard input (see COMMANDS below), so a test
Home Assistant can be shown tampers, mains failures, keypad users and alarms.
"""

from __future__ import annotations

import asyncio
import random
import struct
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _paths  # noqa: E402,F401  (makes custom_components.texecom.* importable without HA)

from custom_components.texecom.connect import protocol as P  # noqa: E402

TEST_ZONES = [
    (1, "Hallway", 1),
    (2, "Lounge", 3),
    (3, "Kitchen", 3),
    (4, "Garage", 3),
    (5, "Landing", 3),
]
DEMO_ZONES = [
    (1, "Front Door", 1),
    (2, "Hallway", 1),
    (3, "Lounge", 3),
    (4, "Kitchen", 3),
    (5, "Patio Door", 4),
    (6, "Garage", 3),
    (7, "Landing", 3),
    (8, "Smoke Detector", 9),
]


class FakeConnectPanel:
    def __init__(self, udl: str = "1234", zones=None, area_name: str = "HOUSE", exit_delay: float = 0.05) -> None:
        self.udl = udl
        self.zone_list = zones or TEST_ZONES
        self.area_name = area_name
        self.exit_delay = exit_delay
        self.zone_state = {n: 0 for n, _name, _t in self.zone_list}
        self.area_state = 0  # index into P.AREA_STATES
        self.part_arm: int | None = None
        self.commands: list[tuple[int, bytes]] = []
        self.writers: set[asyncio.StreamWriter] = set()
        self.msg_seq = 0
        self.nak_next: dict[int, int] = {}
        self.ignore_next: dict[int, int] = {}
        self.clock_offset = timedelta()
        self.clock_set_to: bytes | None = None
        self.server: asyncio.base_events.Server | None = None
        self.port = 0
        self._exit_timer: asyncio.TimerHandle | None = None
        self.on_battery = False
        self.connections = 0  # TCP connections accepted (a SmartCom counts these, logged in or not)
        self.power_override: bytes | None = None  # raw GET_SYSTEM_POWER reply
        self.clock_raw: bytes | None = None  # raw GET_DATE_TIME reply (e.g. an impossible date)
        self.system_flags = bytes(8)  # GET_SYSTEM_FLAGS reply (its meaning isn't mapped yet)
        self.ready: bool | None = None  # area flag 16; None: ready while disarmed with no zone open

    async def start(self, port: int = 0, host: str = "127.0.0.1") -> int:
        self.server = await asyncio.start_server(self._on_client, host, port)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    async def close(self) -> None:
        for w in list(self.writers):
            w.close()
        if self.server:
            self.server.close()

    def drop_all(self, alarm: bool = False) -> None:
        """Panel hangs up (as when it reports an alarm through the module)."""
        if alarm:
            self.area_state = 5
        for w in list(self.writers):
            w.write(b"+++")
            w.close()

    # ─── Unsolicited messages ───────────────────────────────────────────────

    def send(self, body: bytes) -> None:
        frame = P.encode_frame(P.TYPE_MESSAGE, self.msg_seq, body)
        self.msg_seq = (self.msg_seq + 1) & 0xFF
        for w in list(self.writers):
            w.write(frame)

    def set_zone(self, number: int, state: int) -> None:
        self.zone_state[number] = state
        self.send(bytes([P.MSG_ZONE, number, state]))

    def set_area(self, state: int, part_arm: int | None = None) -> None:
        self.area_state, self.part_arm = state, part_arm
        self.send(bytes([P.MSG_AREA, 1, state]))

    def send_log(self, log_type: int, group: int, parameter: int, areas: int = 1) -> None:
        now = datetime.now()
        ts = (
            now.second
            | (now.minute << 6)
            | (now.month << 12)
            | (now.hour << 16)
            | (now.day << 21)
            | ((now.year - 2000) << 26)
        )
        self.send(bytes([P.MSG_LOG, log_type, group, parameter, areas]) + struct.pack("<I", ts))

    def send_zone_alarm(self, number: int) -> None:
        """The zone-alarm log entry. Its type is the zone's type, as on a real
        panel: 1 for an entry/exit zone, 3 for an interior one..."""
        zone_type = next((t for n, _name, t in self.zone_list if n == number), 3)
        self.send_log(zone_type, 3, number)

    def send_user(self, user: int) -> None:
        self.send(bytes([P.MSG_USER, user, 0]))

    # ─── Commands ───────────────────────────────────────────────────────────

    async def _on_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        self.writers.add(writer)
        parser = P.FrameParser(lambda f: self._on_frame(writer, f))
        try:
            while data := await reader.read(1024):
                parser.push(data)
        except (ConnectionError, OSError):
            pass
        finally:
            self.writers.discard(writer)
            writer.close()

    def _on_frame(self, writer: asyncio.StreamWriter, frame: P.Frame) -> None:
        if frame.type != P.TYPE_COMMAND:
            return
        cmd, args = frame.body[0], frame.body[1:]
        self.commands.append((cmd, bytes(args)))

        def reply(payload: bytes) -> None:
            writer.write(P.encode_frame(P.TYPE_RESPONSE, frame.sequence, bytes([cmd]) + payload))

        if self.nak_next.get(cmd, 0) > 0:
            self.nak_next[cmd] -= 1
            reply(bytes([P.NAK]))
            return
        if self.ignore_next.get(cmd, 0) > 0:
            self.ignore_next[cmd] -= 1
            return
        ack = bytes([P.ACK])
        if cmd == P.CMD_LOGIN:
            reply(ack if args.decode("latin1") == self.udl else bytes([P.NAK]))
        elif cmd == P.CMD_SET_EVENT_MESSAGES:
            reply(ack)
        elif cmd == P.CMD_GET_DATE_TIME and self.clock_raw is not None:
            reply(self.clock_raw)
        elif cmd == P.CMD_GET_DATE_TIME:
            t = datetime.now() + self.clock_offset
            reply(P.encode_date_time(t.year, t.month, t.day, t.hour, t.minute, t.second))
        elif cmd == P.CMD_SET_DATE_TIME:
            self.clock_set_to = bytes(args)
            self.clock_offset = timedelta()
            self.clock_raw = None
            reply(ack)
        elif cmd == P.CMD_GET_PANEL_IDENTIFICATION:
            reply(b"Elite 24     V6.05.03LS1".ljust(32))
        elif cmd == P.CMD_GET_ZONE_DETAILS:
            details = bytearray(34)
            for number, name, ztype in self.zone_list:
                if number == args[0]:
                    details[0], details[1] = ztype, 0x01
                    details[2 : 2 + len(name)] = name.encode()
            reply(bytes(details))
        elif cmd == P.CMD_GET_AREA_DETAILS:
            details = bytearray(25)
            details[0] = args[0]
            if args[0] == 1:
                details[1 : 1 + len(self.area_name)] = self.area_name.encode()
            struct.pack_into("<HH", details, 17, 15, 15)
            reply(bytes(details))
        elif cmd == P.CMD_GET_ZONE_STATE:
            start, count = args[0], args[1]
            reply(bytes(self.zone_state.get(start + i, 0) for i in range(count)))
        elif cmd == P.CMD_GET_AREA_FLAGS:
            flags = bytearray(73)
            s = self.area_state
            ready = (
                self.ready if self.ready is not None else s == 0 and not any(v & 3 for v in self.zone_state.values())
            )
            if ready:
                flags[P.FLAG_READY] = 1
            if s == 5:
                flags[P.FLAG_ALARM] = 1
            elif s == 1:
                flags[P.FLAG_EXIT] = 1
            elif s == 2:
                flags[P.FLAG_ARMED] = flags[P.FLAG_ENTRY] = 1
            elif s == 3:
                flags[P.FLAG_ARMED] = flags[P.FLAG_FULL_ARMED] = 1
            elif s == 4:
                flags[P.FLAG_ARMED] = flags[P.FLAG_PART_ARMED] = 1
                flags[P.FLAG_PART_ARM_1 + (self.part_arm or 1) - 1] = 1
            reply(bytes(flags[args[0] : args[0] + args[1]]))
        elif cmd == P.CMD_GET_LCD_DISPLAY:
            reply(b" Premier Elite  " + datetime.now().strftime(" %a %d %H:%M  ").encode())
        elif cmd == P.CMD_GET_SYSTEM_POWER:
            # ref, system V, battery V, system I, battery I (as a real panel:
            # on battery both currents read 0 and the voltage drops)
            if self.power_override is not None:
                reply(self.power_override)
            else:
                reply(bytes([100, 92, 94, 0, 0]) if self.on_battery else bytes([100, 101, 99, 30, 2]))
        elif cmd == P.CMD_ARM_AREA:
            arm_type = args[0]
            reply(ack)
            if arm_type != P.ARM_FULL:
                self.send_log(77 + arm_type, 0, 0)  # PART_ARM_n log, as the panel does
            armed = (3, None) if arm_type == P.ARM_FULL else (4, arm_type)
            if self.exit_delay:
                self.set_area(1)
                self._exit_timer = asyncio.get_running_loop().call_later(self.exit_delay, self.set_area, *armed)
            else:
                self.set_area(*armed)  # no exit time: armed at once
        elif cmd == P.CMD_DISARM_AREA:
            if self._exit_timer:
                self._exit_timer.cancel()
            reply(ack)
            self.set_area(0)
        elif cmd == P.CMD_RESET_AREA:
            reply(ack)
        elif cmd == P.CMD_GET_SYSTEM_FLAGS:
            reply(self.system_flags)
        else:
            reply(bytes([P.NAK]))


COMMANDS = """Commands, one per line:
  zone N open|closed|tamper|alarm    zone N changes (alarm: active and alarmed, and logged)
  area off|exit|entry|armed|alarm    the area changes
  area part N                        the area is part armed with part arm N
  user N                             user N enters a code at a keypad
  mains off|on                       mains fails (logged at once), or comes back (seen in the power readings)
  lid open|closed                    the panel's lid (Panel Box Tamper)
  aux open|closed                    a detector's cover (Auxiliary Tamper, the shared circuit)
  armfail N                          arming failed: zone N was active when the exit time ended
  drop                               hang up, as when the panel reports an alarm
  refuse arm|disarm                  say no to the next arm or disarm from Home Assistant
  log TYPE GROUP PARAMETER [AREAS]   any event-log entry"""

ZONE_BITS = {"open": 0x01, "closed": 0x00, "tamper": 0x02, "alarm": 0x11}
AREA_STATES = {"off": 0, "exit": 1, "entry": 2, "armed": 3, "alarm": 5}
TAMPER_LOGS = {"lid": 60, "aux": 62}
TAMPER_GROUPS = {"open": 11, "closed": 12}


def run_command(panel: FakeConnectPanel, line: str) -> str:
    """Does one command from COMMANDS to the panel; returns what happened."""
    words = line.lower().split()
    try:
        match words:
            case ["zone", n, state] if state in ZONE_BITS:
                panel.set_zone(int(n), ZONE_BITS[state])
                if state == "alarm":
                    panel.send_zone_alarm(int(n))
            case ["area", "part", n]:
                panel.set_area(4, int(n))
            case ["area", state] if state in AREA_STATES:
                panel.set_area(AREA_STATES[state])
            case ["user", n]:
                panel.send_user(int(n))
            case ["mains", "off"]:
                panel.on_battery = True
                panel.send_log(47, 9, 0, areas=0)  # AC Fail, as a real panel logs it
            case ["mains", "on"]:
                panel.on_battery = False  # a real panel doesn't log the restore
            case [place, state] if place in TAMPER_LOGS and state in TAMPER_GROUPS:
                panel.send_log(TAMPER_LOGS[place], TAMPER_GROUPS[state], 0, areas=0)
            case ["armfail", n]:
                panel.send_log(85, 0, int(n))
            case ["drop"]:
                panel.drop_all()
            case ["refuse", "arm" | "disarm" as what]:
                panel.nak_next[P.CMD_ARM_AREA if what == "arm" else P.CMD_DISARM_AREA] = 1
            case ["log", log_type, group, parameter, *areas] if len(areas) <= 1:
                panel.send_log(int(log_type), int(group), int(parameter), *(int(a) for a in areas))
            case _:
                return f"? {line.strip()!r}\n{COMMANDS}"
    except ValueError:
        return f"? numbers only: {line.strip()!r}"
    return f"ok: {line.strip()}"


async def _read_commands(panel: FakeConnectPanel) -> None:
    loop = asyncio.get_running_loop()
    print(COMMANDS, flush=True)
    while line := await loop.run_in_executor(None, sys.stdin.readline):
        if line.strip():
            print(run_command(panel, line), flush=True)


async def _main() -> None:
    port = int(next((a for a in sys.argv[1:] if a.isdigit()), "10001"))
    demo = "--demo" in sys.argv
    panel = FakeConnectPanel(zones=DEMO_ZONES if demo else None, exit_delay=15 if demo else 2)
    if "--clock-reset" in sys.argv:  # as after a full power-down: 31 Oct 2023
        panel.clock_offset = datetime(2023, 10, 31, 12, 0) - datetime.now()
    await panel.start(port, "0.0.0.0")
    print(f"Fake Texecom Connect panel on port {port} (UDL 1234)", flush=True)
    if "--commands" in sys.argv:
        reader = asyncio.create_task(_read_commands(panel))  # noqa: F841  (kept: asyncio holds tasks weakly)
    while True:
        await asyncio.sleep(random.uniform(8, 20) if demo else 3600)
        if demo:
            zone = random.choice([2, 3, 4, 7])
            panel.set_zone(zone, 1)
            await asyncio.sleep(random.uniform(2, 6))
            panel.set_zone(zone, 0)


if __name__ == "__main__":
    asyncio.run(_main())
