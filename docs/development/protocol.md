# What we know about the panel

What the panel and its modules actually do, as seen on a real **Premier Elite 24, firmware V6.05.03, with a SmartCom** (Texecom Connect) and a COM port set to Crestron behind a serial-to-network bridge (2026). Some of it differs from what the protocol's documentation would suggest; the code relies on what's written here.

**Add to this page** whenever you learn something new on a real panel: what you saw, how, the panel and firmware, and the date. The [live test plan](../testing/live-test-plan.md) lists what's still to find out.

**On this page:** [Texecom Connect](#texecom-connect-smartcom-or-comip) · [Area flags](#area-flags) · [System flags](#system-flags) · [Seen by other projects](#seen-by-other-projects) · [Crestron](#crestron) · [Still unknown](#still-unknown)

## Texecom Connect (SmartCom or ComIP)

The protocol follows Joseph Heenan's [texecom-connect](https://github.com/davidMbrooke/texecom-connect) (Apache-2.0); arming and the zone and area reads follow [texecom2mqtt](https://github.com/dchesterton/texecom2mqtt-hassio) (MIT). Code: `connect/protocol.py` (bytes), `connect/client.py` (the session).

### Frames

`'t' | type | length | sequence | body… | crc8`: type `C` command, `R` response, `M` unsolicited message; length counts the whole frame; CRC-8 with polynomial 0x85, initial 0xFF, over everything before it.

### The session

| Behaviour | Detail | Where in the code |
|---|---|---|
| **One session at a time** | The SmartCom serves Home Assistant *or* the Texecom app, Homebridge, texecom2mqtt | — |
| **Refuses a new login for ~10–70 s after a session closes** | Setup's check, a Home Assistant restart, a reload: the next login is refused or the connection is closed during login. Retries are expected; the first three are logged at debug level | `connect/panel.py` (`QUIET_FAILURES`), `connect/discovery.py` (`probe` waits up to 75 s) |
| **A login sent too soon is ignored** | Wait 2 s after connecting before sending it | `client.py` (`LOGIN_DELAY`) |
| **Drops an idle session after ~60 s** | A keep-alive every 30 s; the driver uses it to re-read zones, areas, the keypad text and power | `client.py` (`KEEPALIVE`), `connect/panel.py` (`_on_idle`) |
| **Slow answers** | One command at a time; resent (same sequence number) after 3.5 s, up to 5 times. `GET_AREA_FLAGS` once took over 2.5 s | `client.py` |
| **Busy: a 1-byte NAK (0x15)** | Right after a burst of events, reads can get a NAK. Taken as data it would read as "zone active, alarmed", so short replies mean "busy, change nothing" | `client.py` (`PanelBusyError`) |
| **Hangs up to report an alarm** | The SmartCom drops the session (with `+++`, or by closing it) to send its own alarm report, and lets nothing back in for about 2 minutes. In two alarms on 8 Oct 2026 it closed the session about 1.5 s **after the disarm** that followed the alarm, not when the alarm started, just after the alarm's log entries marked *communicated*; Home Assistant was back in 2 min 8 s later both times. Logins meanwhile got 80-byte frames the driver doesn't recognise and frames with bad CRCs, then the SmartCom closed them | `protocol.py` (`FrameParser`), `panel.py` (`OFFLINE_GRACE`, 3 minutes) |
| **Wintex alongside** | Wintex can connect through the SmartCom while Home Assistant is connected | — |

### Commands used

| # | Command | Notes |
|---|---|---|
| 1 | Login | UDL code as text. ACK, or NAK for a wrong code |
| 2 | Get zone state | One byte per zone (layout below); up to 168 zones per request |
| 3 | Get zone details | Type, area bitmap, name. Type 0 = zone not used |
| 6 | Arm area | Arm type (0 full, 1–3 part arm) + area bitmap |
| 8 | Disarm area | Area bitmap |
| 9 | Reset area | Sent before disarming when in alarm |
| 10 | Get system flags | 8 bytes, meaning not mapped yet ([first clues](#system-flags)). Only read for diagnostics, as an *optional* command: sent once, and no answer doesn't end the session |
| 11 | Get area flags | Bulk: 72 flags in 0.4 s on the Elite 24 (see [Area flags](#area-flags)). Some firmware (Elite 48, V4.02.01) answers a bulk read with one byte: then flags are read one at a time |
| 13 | Get LCD display | The keypad's two 16-character lines, with the clock (e.g. `HOME 13:48.52 Wed 07`), which the driver strips. Seen: `HOME`, `Area in Entry > A.`, `Z003 Secure Kitchen`, `AUX 0,0 Tamper 08:40.38 08/10`, `System Alerts!`, `Alarm Engineer Working On Site.` Read every 30 s, so short messages such as *Area arm fail* are usually missed |
| 22 | Get panel identification | e.g. `Elite 24     V6.05.03LS1` |
| 23 / 24 | Get / set date and time | Day, month, 2-digit year, hours, minutes, seconds |
| 25 | Get system power | Reference, panel volts, battery volts, panel current, battery current (formula below) |
| 35 | Get area details | Name, exit and entry delays |
| 37 | Set event messages | Subscribes to zone, area, output, user and log messages |

### Messages the panel sends

| Message | Content |
|---|---|
| **Zone** | Zone number and a state byte: bits 0–1 secure/active/tamper/short, bit 2 fault, bit 3 failed test, bit 4 **alarmed**, bit 5 manual bypass, bit 6 auto bypass, bit 7 masked |
| **Area** | Area and state: 0 disarmed, 1 in exit, 2 in entry, 3 armed, 4 part armed, 5 in alarm. **6 and 7** arrive straight after Part Arm 1 and 2 (a "settled" part arm?): the driver re-reads the area flags instead. On 8 Oct 2026 state 6 came with log entries 113 (*Remote Command*, group 9, parameter 1) and 207 (*Remote Part Arm 1*, group 6). Neither value is in the published lists, so the debug log shows `unknown (6)` |
| **User** | User number and method (code, tag, code + tag) when someone uses a keypad |
| **Log** | Event type, group (low 6 bits; bit 6 "communication delayed", bit 7 "communicated"), parameter (a zone or user number), areas, timestamp |
| **Output** | Output location and state: decoded but not used yet |

**Groups** start or end things: 1 priority alarm, 3 alarm, 9 maintenance alarm, 11 tamper alarm, 20 fault start them; 2, 4, 10, 12 are the matching restores.

### Log event types the integration uses

| Type | Meaning | Handled in |
|---|---|---|
| 1–21 | Zone events: an alarm (group 3), tamper alarm (group 11) or fire alarm (raw group 129) names the zone | `connect/events.py` |
| 39 | Auto open/close | `connect/events.py` (re-reads the areas) |
| 47 | AC Fail (mains). **The restore is never logged** | `connect/conditions.py` |
| 48, 50–52, 65, 66, 96, 97, 99, 101, 104, 105, 107–109, 118, 119, 122 | Faults: Low Battery, Mains Over Voltage, Telephone Line Fault, Fail to Communicate, Expander/Keypad Trouble, Supervision Fault, RF Low Battery, Radio Jamming, Zone Fault, Zone Masked, PSU faults… | `connect/conditions.py`, names in `protocol.py` (`FAULT_LOG_NAMES`) |
| 59 | Installer (engineer) programming ended: the driver re-reads zones and areas | `connect/events.py` |
| 60 | **Panel Box Tamper** (the lid): group 11 open, 12 closed | `connect/conditions.py` |
| 62 | **Auxiliary Tamper**: every detector's tamper on one shared circuit (both PIR types tested), so nothing can say which detector. Group 11 (marked *communicated*) when a cover opens, 12 when it's closed | `connect/conditions.py` |
| 61, 63, 64, 67, 68, 70, 110, 121 | Other tampers: bell, expander, keypad, fire zone, zone, code tamper, PSU, GSM | `connect/conditions.py`, names in `protocol.py` (`TAMPER_LOG_NAMES`) |
| 78–80, 204–209 | Part Arm 1–3 (says which part arm a "part armed" area message means) | `connect/events.py` |
| 85 | **Arm failed**: one entry per zone still active at the end of the exit time; the keypad shows *Area arm fail* | `connect/events.py` |

### Area flags

`Get area flags` returns one area bitmap per flag. Texecom's protocol specification names all 73; the names are in `connect/protocol.py` (`AREA_FLAG_NAMES`, from the texecom-connect forks), and the debug log and diagnostics show the flags set for each area by name, so the rest can be mapped on a real panel ([live test plan](../testing/live-test-plan.md#part-d--mapping-the-protocol), D12).

| Flag | Name | Used for | Status |
|---|---|---|---|
| 0 | Alarm | *Alarm!* on a re-read | Used from the start (texecom2mqtt's layout). Another project saw it act as alarm *memory* on V4 firmware (below): check on this panel (D15) |
| 16 | Ready | **Ready to arm** | New: confirm on a real panel (D12) |
| 17, 18 / 19 | Entry, Second Entry / Exit | *Entry delay* / *Arming…* on a re-read | Used. This panel showed a stale Exit flag just after a remote arm (the driver ignores it while armed) |
| 21, 22, 23, 26 | Armed, Full Armed, Part Armed, Force Armed | the armed states on a re-read | Used |
| 24 | Part Arming | *Arming…* on a re-read | Used |
| 50–52 | Part Arm 1–3 | which part arm | Used |
| 14 | Tamper Alarm | — | Candidate for tampers already open when Home Assistant connects (D11) |
| 28, 29, 30 | Bell SAB, Bell SCB, Strobe | — | Candidates for "the siren is sounding" |
| 36 | Reset Required | — | Candidate for *System Alerts!* |
| 64, 65, 66 | Detector Fault, Detector Masked, Fault Present | — | Candidates for faults already present when Home Assistant connects (D11) |

**With the mains off** (one read, 8 Oct 2026) the flags set were 29 *Bell SCB*, 32 *Detector Reset* and 67 *LED control*; 16 *Ready* and 25 *Force Armable* were clear. Three minutes later, still on battery, *Ready* was set again, so a zone active at the time of that read may explain it: check again with nothing moving.

### System flags

`Get system flags` (command 10) returns 8 bytes whose meaning isn't published. First clues from one panel (8 Oct 2026):

| State | Bytes |
|---|---|
| Normal | `00 20 09 00 00 00 00 01` |
| Once, after an alarm and before the engineer code (the keypad showed *Alarm Engineer Working On Site*) | byte 0 was `40`. A second alarm, disarmed with a user code, left it at `00`, so it isn't simply "after an alarm" |
| Mains off | byte 2 was `08` |
| Mains on, straight after the engineer code cleared *System Alerts!* | byte 2 was also `08`, so bit 0 of byte 2 isn't simply *mains OK* |

None of these is understood yet, and nothing relies on them. They're candidates for knowing what's already wrong when Home Assistant connects (D11): capture them with a tamper open, a fault, during an alarm, and before and after clearing *System Alerts!* (D13).

### Arming, alarms and the keypad

- **Every arm path works**: Away, Part Arm 1 and 2, switching mode, keypad arms (with the user number), and Apple Home through Home Assistant's HomeKit Bridge.
- **Switching mode** (disarm, then arm) really disarms for a moment: *disarmed* about 0.4 s after the request, then *in exit* 0.3 s later, with log 42 (*Remote Open/Close*) group 5 (*Open*) for the disarm. The driver hides it, so *disarmed* automations don't fire (`connect/panel.py`, `SWITCH_GRACE`).
- **A flag re-read just after a remote arm** can still show the exit flag; the driver ignores an exit flag on an armed area.
- **Exit times**: with the same programmed exit delay, a remote arm set after about 10 s and a keypad arm after 15 s (option 58 *Remote Arm Instant* on, which seems to shorten the exit time rather than skip it). The entry delay was 15–16 s.
- **Fail to set**: a zone active at the end of the exit time stops the arm and sounds the siren; log type 85 names each zone, and the keypad shows *Area arm fail*. With detectors only (no door contacts), keep moving in view of one until the exit time ends.
- **Zone alarms are logged twice** (again once reported). Sometimes only the second arrives, after the disarm. The zone's *alarmed* bit is set at once and stays set (alarm memory) until reset, so a zone in a disarmed area doesn't explain a later alarm.
- **After an alarm the panel can go back to *in entry*** when the entry zone is seen again: *in alarm*, *in entry* 1.6 s later, then *in alarm* again 16 s after that, with the sirens sounding throughout until the disarm. The zone stays *alarmed*. The driver keeps showing the alarm (`connect/panel.py`, `_apply_area`).
- **A detector that sees the entry route can raise a real alarm**: walking in, an interior PIR saw the person 0.6 s before the entry zone did, so the area went *in alarm* (keypad sounder only; the alarm was reported) and 0.6 s later *in entry*.
- **After an alarm or a tamper**, clearing *System Alerts!* at the keypad needed the engineer code on this panel (it shows as user 0 in the *user* message); the spanner light stayed on afterwards (probably a service reminder).

### Power

- **Readings**: volts = 13.7 + (reading − reference) × 0.07; current = reading × 9 mA.
- **On mains**: about 300 mA (and 18 mA into the battery) at about 13.6 V. **On battery**: both currents read 0, and the voltage falls (13.0 → 12.2 V in 2 minutes once; 13.6 → 12.2 V over 9 minutes, the battery 13.6 → 12.4 V, another time).
- The **AC Fail** log arrives at once, but the restore never does, so the driver decides mains is back from the readings (`connect/conditions.py`): within 30 s, as they're taken every 30 s (7 s on 8 Oct 2026). Panels that never report current are only called "on battery" below 12.9 V.

### Clock

- A **total power loss** (mains and battery) reset the clock to **31 Oct 2023**.
- The clock can hold an **impossible date** (day 0, month 0): treated as "not set".
- The panel keeps local wall-clock time; the integration uses Home Assistant's time zone, not the computer's (a Docker container on UTC would otherwise put the panel an hour out in summer).

## Seen by other projects

Reported by other Texecom projects on their panels, not yet seen on this one. Useful to know, and worth checking (sources in [Other projects](other-projects.md)):

- **Busy NAKs can last**: after a burst of events (~50 s of them), an Elite 88 (V6.02.02, ComIP) answered the keep-alive with a NAK on every retry. Not a dead session. The driver here treats a NAK as "busy, change nothing", so it doesn't reconnect.
- **One connection, enforced at TCP**: a second connection is refused outright while one is live. After a session ends, a ComIP accepted a new login after 2 s (this SmartCom: 10–70 s).
- **A client holding its own dead socket can't log in again** until that socket is closed. The driver closes a dropped session's socket straight away and keeps hold of the task that does it.
- **The alarm-time drop is the SmartCom's**: on a dedicated ComIP the session stayed up through an alarm. A SmartCom session can also carry modem commands (`ATH0`, `ATZ`) when the SmartCom reports.
- **"Disarmed" not always sent**: after disarming Part Arm 2 on an Elite 88, the area message didn't always arrive. The keep-alive re-read corrects it here within 30 s.
- **Flag 0 as alarm memory**: on an Elite 48 (V4.02.01) flag 0 was clear while the alarm sounded and set after the disarm; flags 5, 28, 30, 44, 61 and 62 were the ones set while it sounded. If this panel did the same, a re-read after a keypad disarm would show *Alarm!* again (D15).
- **Arm and disarm "as a user"** (commands 29 and 30) apply that user's rights: an "Arm Only" user's code armed but couldn't disarm. Not used here.
- **Get user** (command 27, the user number as one byte) returns a user's name **and code** and settings: 23 bytes on a Premier Elite 24 (V6.05). The name is the first 8 bytes; the code follows as BCD, all `FF` for a user without one. A NAK past the last user (25 on a 24-user panel). The driver reads only the name (`decode_user_name`), when asked to fill in the names in the options; the rest of the reply is never decoded, kept or logged, and a reply with a bad CRC isn't shown in the debug log.

## Crestron

A COM port set to *Crestron System*: text lines, plus binary UDL (Wintex) frames inside a UDL session. Code: `crestron/protocol.py`, `crestron/connection.py`, `crestron/panel.py`.

| Line | Means |
|---|---|
| `"Z0071` | Zone 007 active (`1`) or clear (`0`) |
| `"A0013` / `"D0013` | Area 001 armed / disarmed by user 3 (the user number has no fixed width; `0` seems to mean nobody) |
| `"L0010` / `"X0010` / `"E0010` | Area 001 in alarm / exit delay started / entry delay started |
| `"U0030` | User 003 entered a code |
| `"NY` | ASTATUS reply: one letter per area, Y armed, N not |
| `OK` / `ERROR` | Command acknowledgement |

- **Commands** are `\<command>/`, e.g. `\W1234/` (UDL login, opens a Wintex session), `\A<bitmask>/` (arm), `\D<bitmask>/` (disarm). `ASTATUS` is sent as a plain line.
- **The UDL login often gets no `OK`** although the session opens (a real panel took 3–6 s when it did answer). Then **text commands get `ERROR` while binary frames work**, so the driver falls back to the binary frame.
- **A UDL session silences the text feed.** Logging out (binary `H` frame) shortens that to about 30 s; the held-back events then arrive in one burst (e.g. `X`, `A`, `D` for an arm and a quick disarm), which the driver coalesces.
- **Part arms** use the binary `S` frame (area index, part arm 1–3), confirmed for area 1 only.
- **Keypad emulation (`KEY`)** is ignored on V6.05.03. **UDL memory addresses** differ between firmware versions, so the integration doesn't read memory.
- **ASTATUS** doesn't report the exit delay or the end of an alarm.

## Still unknown

Things to find out on real panels (each is a task in the [live test plan](../testing/live-test-plan.md#part-d--mapping-the-protocol)):

- What **area states 6 and 7** mean exactly, and whether other values exist.
- **Output messages**: which outputs (bell, strobe, others) report, and what their states mean.
- **Log event types** not in the tables above, including **type 137** (observed during testing, meaning unknown).
- Whether a remote **arm is refused** (NAK) with a zone open or a fault present, and what the panel sends then.
- Commands not used yet that could help: **reading the event log** (to catch up after a reconnect), **user names** (its reply includes the code), **zone bypass**, **outputs**, **the keypad text** (command 14).
- What the **system flags** (command 10) mean beyond the [first clues](#system-flags), and which **area flags** show tampers and faults that are already there when Home Assistant connects (D11–D13).
- Whether **Ready** (flag 16) is set exactly when the area can be armed, and what it shows while armed.
- Whether Home Assistant's own login writes **Download Start** (log 53) to the panel's log, before log 53/54 (remote access) and 58 (engineer programming) are shown in the activity list.
- The exact **refusal window** after a session closes, and what affects it.
- Why the SmartCom waited for the disarm before reporting an alarm (and dropping the session), and whether that depends on how the panel is set to report alarms.
- The SmartCom's **network name (DHCP hostname)**, which could let Home Assistant discover it automatically.
- Whether the panel changes its own clock for **summer time**, and how that interacts with clock sync (UK clocks change on 25 October 2026 and 28 March 2027).
- **Other panels and modules**: Elite 12, 48, 64, 88, 168, 640; firmware V4 and other V6 versions; ComIP; more than one area.

---

[← Testing](testing.md) · [Live test plan →](../testing/live-test-plan.md)
