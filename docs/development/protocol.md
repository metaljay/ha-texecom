# What we know about the panel

What the panel and its modules actually do, as seen on a real **Premier Elite 24, firmware V6.05.03, with a SmartCom** (Texecom Connect) and a COM port set to Crestron behind a serial-to-network bridge (2026). Some of it differs from what the protocol's documentation would suggest; the code relies on what's written here.

**Add to this page** whenever you learn something new on a real panel: what you saw, how, the panel and firmware, and the date. The [live test plan](../testing/live-test-plan.md) lists what's still to find out.

**On this page:** [Texecom Connect](#texecom-connect-smartcom-or-comip) · [Area flags](#area-flags) · [System flags](#system-flags) · [Seen on testers' panels](#seen-on-testers-panels) · [Seen by other projects](#seen-by-other-projects) · [Crestron](#crestron) · [Still unknown](#still-unknown)

## Texecom Connect (SmartCom or ComIP)

The protocol follows Joseph Heenan's [texecom-connect](https://github.com/davidMbrooke/texecom-connect) (Apache-2.0); arming and the zone and area reads follow [texecom2mqtt](https://github.com/dchesterton/texecom2mqtt-hassio) (MIT). Code: `connect/protocol.py` (bytes), `connect/client.py` (the session).

### Frames

`'t' | type | length | sequence | body… | crc8`: type `C` command, `R` response, `M` unsolicited message; length counts the whole frame; CRC-8 with polynomial 0x85, initial 0xFF, over everything before it.

### The session

| Behaviour | Detail | Where in the code |
|---|---|---|
| **One session at a time** | The SmartCom serves Home Assistant *or* the Texecom app, Homebridge, texecom2mqtt | — |
| **Refuses a new login for ~10–70 s after a session closes** | Setup's check, a Home Assistant restart, a reload: the next login is refused or the connection is closed during login. Retries are expected, so they're logged at debug level, with one warning only after 3 minutes without a connection (as long as entities keep their state) | `connect/panel.py` (`_run`, `OFFLINE_GRACE`), `connect/discovery.py` (`probe` waits up to 75 s) |
| **A login sent too soon is ignored** | Wait 2 s after connecting before sending it. Nothing else is sent until the login (and the event subscription) is ACKed: a read or re-read due meanwhile (a timer from the last session) is refused and tried again after the login | `client.py` (`LOGIN_DELAY`, `command`) |
| **Drops an idle session after ~60 s** | A keep-alive every 30 s; the driver uses it to re-read zones, areas, the keypad text and power | `client.py` (`KEEPALIVE`), `connect/panel.py` (`_on_idle`) |
| **Slow answers** | One command at a time; resent (same sequence number) after 3.5 s, up to 5 times. `GET_AREA_FLAGS` once took over 2.5 s | `client.py` |
| **Busy: a 1-byte NAK (0x15)** | Right after a burst of events, reads can get a NAK. Taken as data it would read as "zone active, alarmed", so short replies mean "busy, change nothing" | `client.py` (`PanelBusyError`) |
| **Hangs up to report an alarm** | The SmartCom drops the session (with `+++`, or by closing it) to send its own alarm report, and lets nothing back in for about 2 minutes. In two alarms on 8 Oct 2026 it closed the session about 1.5 s **after the disarm** that followed the alarm, not when the alarm started, just after the alarm's log entries marked *communicated*; Home Assistant was back in 2 min 8 s later both times. After an alarm on 20 Oct 2026 (armed Away through the Crestron port, disarmed 2 s into the alarm), it was back about 75 s after the disarm. Logins meanwhile got 80-byte frames the driver doesn't recognise and frames with bad CRCs, then the SmartCom closed them | `protocol.py` (`FrameParser`), `panel.py` (`OFFLINE_GRACE`, 3 minutes) |
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
| 10 | Get system flags | 8 bytes, meaning not mapped yet ([first clues](#system-flags)). Only read for diagnostics, as an *optional* command: sent at most twice (a panel busy with a burst of events can miss one), and no answer doesn't end the session |
| 11 | Get area flags | Bulk: 72 flags in 0.4 s on the Elite 24 (see [Area flags](#area-flags)). Some firmware (Elite 48, V4.02.01) answers a bulk read with one byte: then flags are read one at a time |
| 13 | Get LCD display | The keypad's two 16-character lines, with the clock (e.g. `HOME 13:48.52 Wed 07`), which the driver strips. Seen: `HOME`, `Area in Entry > A.`, `Z003 Secure Kitchen`, `AUX 0,0 Tamper 08:40.38 08/10`, `System Alerts!`, `Alarm Engineer Working On Site.` Read every 30 s, and about 1 s after an area message, a failed arm, a tamper or a fault (at most every 5 s), so short messages such as *Area arm fail* are caught |
| 15 / 16 | Get log pointer / Get log events | The stored event log, read after each reconnect: the entries written since the last login (at most the last 30, 5 per read; the layout is under [Crestron](#crestron)), skipping those that came live (same type, group, parameter, areas and time: the two match to the second). Tamper and fault starts and ends are applied, and settings changes make the driver read the zones again; zone alarms and failed arms aren't, as late they'd look like new ones (and a zone alarm is logged again once reported). The first login after Home Assistant starts only reads the pointer. Both are *optional* commands: a panel that doesn't answer isn't asked again, and the log is only read where its layout is known (24, 48, 88 and 168 zones). **Confirmed on the Premier Elite 24 (V6.05.03) over the SmartCom** (20 Oct 2026): the pointer answered (431 of the 500-entry ring), and two reads of 5 gave 8-byte entries that matched what had just happened (remote command, exit started, remote close, an interior alarm, bell and alarm active, the user's code and disarm, the restore, an engineer code), to the second |
| 22 | Get panel identification | e.g. `Elite 24     V6.05.03LS1` |
| 23 / 24 | Get / set date and time | Day, month, 2-digit year, hours, minutes, seconds |
| 25 | Get system power | Reference, panel volts, battery volts, panel current, battery current (formula below) |
| 35 | Get area details | Name, exit and entry delays |
| 37 | Set event messages | Subscribes to zone, area, output, user and log messages |

### Messages the panel sends

| Message | Content |
|---|---|
| **Zone** | Zone number and a state byte: bits 0–1 secure/active/tamper/short, bit 2 fault, bit 3 failed test, bit 4 **alarmed**, bit 5 manual bypass, bit 6 auto bypass, bit 7 masked |
| **Area** | Area and state: 0 disarmed, 1 in exit, 2 in entry, 3 armed, 4 part armed, 5 in alarm. **6, 7 and 8** arrive straight after the *part armed* (4) for Part Arm 1, 2 and 3 (5 + the part arm; Part Arm 2 remote and Part Arm 3 at the keypad on 14 Oct 2026): the driver re-reads the area flags instead. A V5.02 panel [didn't send them](#seen-on-testers-panels). On 8 Oct 2026 state 6 came with log entries 113 (*Remote Command*, group 9, parameter 1) and 207 (*Remote Part Arm 1*, group 6). Neither value is in the published lists, so the debug log shows `unknown (6)` |
| **User** | User number and method (code, tag, code + tag) when someone uses a keypad |
| **Log** | Event type, group (low 6 bits; bit 6 "communication delayed", bit 7 "communicated"), parameter (a zone or user number), areas, timestamp |
| **Output** | Output location and state: decoded but not used yet |

**Groups** start or end things: 1 priority alarm, 3 alarm, 9 maintenance alarm, 11 tamper alarm, 20 fault start them; 2, 4, 10, 12 are the matching restores.

### Log event types the integration uses

| Type | Meaning | Handled in |
|---|---|---|
| 1–21 | Zone events: an alarm (group 3), tamper alarm (group 11) or fire alarm (raw group 129) names the zone | `connect/events.py` |
| 39 | Auto open/close | `connect/events.py` (re-reads the areas) |
| 47 | AC Fail (mains). **The restore is logged late, or not at all**: on 8 Oct 2026 two AC alarms (08:46:22, 08:51:23) were followed by *Mains AC Restore* only at 08:55:17; on 7 Oct an AC alarm (14:19:31) had no restore in the log at all. The driver takes the restore from the power readings instead | `connect/conditions.py` |
| 48, 50–52, 65, 66, 96, 97, 99, 101, 104, 105, 107–109, 118, 119, 122 | Faults: Low Battery, Mains Over Voltage, Telephone Line Fault, Fail to Communicate, Expander/Keypad Trouble, Supervision Fault, RF Low Battery, Radio Jamming, Zone Fault, Zone Masked, PSU faults… | `connect/conditions.py`, names in `protocol.py` (`FAULT_LOG_NAMES`) |
| 59, 54, 100 | Installer (engineer) programming ended at a keypad (59), a Wintex session ended (54 *Download End*), or settings were written (100 *Site Data Changed*, one per write, parameter 1): the driver re-reads zones and areas, which any of these may have changed (after 100, once the writes have stopped for 5 s). **Through a serial adapter on the Crestron port, Wintex sessions never logged Download End** (12 Oct 2026, and the older entries in the event log show the same), so 100 is the dependable signal; 113 *Remote Command* (group 9, parameter = the Com Port, 3 here) comes with each write. **A full send from Wintex logged no 100 at all** (only 53 when it connected), so then nothing tells the driver: the user reads the zones again from the options. **Reading zone details while Wintex was connected ended Home Assistant's session**, and the SmartCom then turned logins away (80-byte frames) until Wintex disconnected, though the usual status reads carried on during other Wintex sessions. So a re-read that fails is retried after the next login, and a zone the driver knows is never dropped because its details couldn't be read (`connect/discovery.py`) | `connect/events.py` |
| 60 | **Panel Box Tamper** (the lid): group 11 open, 12 closed | `connect/conditions.py` |
| 62 | **Auxiliary Tamper**: every detector's tamper on one shared circuit (both PIR types tested), so nothing can say which detector. Group 11 (marked *communicated*) when a cover opens, 12 when it's closed. **Not always logged**: on 8 Oct 2026 the third opening that day (10:45, after two at 08:40 and 08:42) sent no group 11 at all, although the keypad showed `AUX 0,0 Tamper` and the internal sounder went off; only the group 12 came, when the cover closed. A limit on repeated alarms ("swinger" count)? The driver falls back on area flag 44 ([below](#area-flags)) | `connect/conditions.py` |
| 61, 63, 64, 67, 68, 70, 110, 121 | Other tampers: bell, expander, keypad, fire zone, zone, code tamper, PSU, GSM | `connect/conditions.py`, names in `protocol.py` (`TAMPER_LOG_NAMES`) |
| 78–80, 204–209 | Part Arm 1–3 (says which part arm a "part armed" area message means) | `connect/events.py` |
| 85 | **Arm failed**: one entry per zone still active at the end of the exit time; the keypad shows *Area arm fail* | `connect/events.py` |

### Matched against the panel's own event log

On 12 Oct 2026 the panel's event log (460 entries, read in Wintex) was matched, to the second, with what the driver had received live during the tests of 8 Oct and that day. Every live log message had the same meaning as the Wintex entry at that time, and the names in `protocol.py` agree. What the parameters and groups mean there:

| Type, group | Wintex shows | Parameter |
|---|---|---|
| 31, 0 | *System Access by User nn* | The user (0 the engineer, whose areas are A and B: bitmap 3) |
| 32, 16 / 32, 17 | *Exit Started* / *Exit Stopped* (groups 16 and 17 mean started and stopped here, not *Armed* and *Tested*) | Where: **0** a remote arm (Home Assistant), **11** the keypad *Net 1 – Rkp 1*. The log also shows *Net 2 – Rkp 1* once (presumably 21) |
| 33, 5 / 33, 6 | *Exit Error – Arming Failed Alarm* / *… Restore* | 0 |
| 34, 18 | *Entry started by Zone nnn* | The zone |
| 37, 5 | *Area(s) DISARMED* (by a user at the keypad) | The user |
| 42, 6 / 42, 5 | *Area(s) Remote ARMED* / *Remote DISARMED* | 0 |
| 53, 9 | *Download Start* (Wintex connecting) | The Com Port Wintex came in through (3: the Crestron port, through a serial-to-network adapter; 113 *Remote Command* carries the same). Its areas were A (1) while only area A had zones, A and B (3) while a zone was in area B |
| 58, 9 / 59, 9 | *Installer Programming Start* / *End* | 0 |
| 62, 11 / 62, 12 | *Auxiliary Tamper Alarm / Restore from Control Panel* | 0 |
| 85, 0 | *Zone nnn Arm Failed* | The zone |
| 106, 0 | *Faults Overridden* | 0 |

Also learnt from the comparison:

- **Home Assistant logging in doesn't write to the event log.** Its many logins over the SmartCom left no *Download Start* (53); only Wintex did.
- **The event log can miss what was sent live.** An alarm on 8 Oct (entry by zone 1, the Kitchen in alarm, disarmed by user 3, panel time 14:30:50–14:30:58) arrived over Connect as log types 34, 31, 3, 37 and 3 (restore) but isn't in the event log at all. Don't treat the event log as complete.
- **The panel's clock ran about 7 s ahead** of Home Assistant that day (clock sync was off): log timestamps are the panel's clock, not when the message arrived.
- **In the event log but not yet seen live** (so their type numbers are still to confirm): *Bell Active*, *Alarm Active*, *Alarm Abort*, *Confirmed Intruder*, *Confirmed Alarm*, *Zone nnn Locked Out*, *Remote Command Via Com Port*, *Site Data Changed (via Wintex)*, *Date/Time Changed by Remote PC*, *Manual Test Transmission*, *ARC 1 Test Call via Com-IP Passed*, *ARC ATS Fault*, *Panel Lid Tamper*, *Mains AC Restore*, *Area(s) Part ARMED n by User nn*.

### Walk test

Seen live on 12 Oct 2026 (Premier Elite 24, V6.05.03), a walk test run from the engineer menu, with every zone passing:

- It sits inside engineer programming: log 31 (user 0, areas 3), **58** *Installer Programming Start*, … , **59** *End*, after which the driver re-reads the zones.
- **Log 74** *User Walk Test*: **group 13** when it starts (parameter 0, areas 3), **group 15** the first time each zone is seen (parameter = the zone; later sightings aren't logged), **group 14** when it ends.
- **Area flag 33** *Walk Test* is set for the whole test (and cleared at its end). Zone messages are the usual active/secure ones; the *failed test* bit (3) stayed clear.

### Zone types

The type number the panel reports (zone details, and the type of a zone's alarm log entry) is the position in Wintex's *Zone Type* list, confirmed on a Premier Elite 24 (V6.05.03):

| No. | Type | No. | Type | No. | Type |
|---|---|---|---|---|---|
| 0 | Not used | 8 | PA Silent | 16 | Latch Key |
| 1 | Entry/Exit 1 | 9 | Fire | 17 | Security |
| 2 | Entry/Exit 2 | 10 | Medical | 18 | Omit Key |
| 3 | Guard | 11 | 24hr Gas | 19 | Custom |
| 4 | Guard Access | 12 | Auxiliary | 20 | Conf PA Audible |
| 5 | 24hr Audible | 13 | Tamper | 21 | Conf PA Silent |
| 6 | 24hr Silent | 14 | Exit Terminator | | |
| 7 | PA Audible | 15 | Moment Key | | |

`binary_sensor.py` uses 9 (smoke), 11 (gas), 7, 8, 20, 21 (panic) and 13 (tamper). The type says nothing about the detector (a door contact and a PIR can both be *Guard*), hence the name-based guesses.

### Area flags

`Get area flags` returns one area bitmap per flag. Texecom's protocol specification names all 73; the names are in `connect/protocol.py` (`AREA_FLAG_NAMES`, from the texecom-connect forks), and the debug log and diagnostics show the flags set for each area by name, so the rest can be mapped on a real panel ([live test plan](../testing/live-test-plan.md#part-d--mapping-the-protocol), D12).

| Flag | Name | Used for | Status |
|---|---|---|---|
| 0 | Alarm | *Alarm!* on a re-read | Used from the start (texecom2mqtt's layout). Another project saw it act as alarm *memory* on V4 firmware (below): check on this panel (D15) |
| 16 | Ready | **Ready to arm** | New: confirm on a real panel (D12) |
| 17, 18 / 19 | Entry, Second Entry / Exit | *Entry delay* / *Arming…* on a re-read | Used. This panel showed a stale Exit flag just after a remote arm: Exit counts only while Armed (21) is clear |
| 21, 22, 23, 26 | Armed, Full Armed, Part Armed, Force Armed | the armed states on a re-read | Used. **21 is clear during the exit delay and set from the moment the area arms**; 23 comes about 15 s later. After a disarm, 23 and 60 *Armed/Alarm* stay set for a few seconds without 21 (the driver ignores flags just after a disarm) |
| 24 | Part Arming | *Arming…* on a re-read, while Armed (21) is clear; **in an exit delay, a part arm** (with 50–52 for which) | Used. Set with 19 *Exit* (seen within 1 s of the *in exit* message) during a part arm's exit delay, never during a full arm's (keypad Part Arm 1, 2 and Away, 14 Oct 2026), and **stayed set all the time Part Arm 2 or 3 was armed** (both have nothing programmed on this panel; not seen with Part Arm 1), so it alone made a reconnect show *Arming…* until the disarm (fixed 14 Oct 2026) |
| 50–52 | Part Arm 1–3 | which part arm, and in an exit delay which one it's arming to | Used. Set from the start of a part arm's exit delay, with 24 |
| 63 | Full Armed/Exit | **In an exit delay, a full arm** (Away) | Used. Set about 1.5 s after a full arm's exit delay started (keypad, 14 Oct 2026; 2 s in on another try), never during a part arm's, and stayed set about 1.3 s after a disarm. Also seen 0.35 s after a disarm on 8 Oct 2026 |
| 44 | Internal Alarm | **A tamper the panel doesn't log**: while an area is disarmed, flag 44 set for 5 s with nothing reported to explain it (a tamper, a zone alarm, a failed arm; the area in exit, entry or alarm) shows as the tamper *Internal Alarm* until it clears | Seen 8 Oct 2026: set 1 s after a detector's cover opened (with 62 *Speaker Mimic*), while disarmed, with no tamper logged; both cleared once a user code was entered, not when the cover closed. On V4 firmware another project saw it set while an alarm sounded (below). What else sets it is to be checked (exit and entry tones, chime, the fail-to-set warning, walk test): the driver ignores it in exit, entry and alarm, and after a failed arm |
| 62 | Speaker Mimic | — | Set with 44 above. Not used: it may follow exit, entry or chime tones too |
| 14 | Tamper Alarm | — | Candidate for tampers already open when Home Assistant connects (D11). Not set while the cover was open above |
| 28, 29, 30 | Bell SAB, Bell SCB, Strobe | — | Candidates for "the siren is sounding" |
| 36 | Reset Required | — | Candidate for *System Alerts!* |
| 64, 65, 66 | Detector Fault, Detector Masked, Fault Present | — | Candidates for faults already present when Home Assistant connects (D11) |
| 27, 55 | Arm Failed, Arm Fail Warning | — | Seen 8 Oct 2026: 27 set (with 30 *Strobe*, 44 and 62) while the "fail to set" warning sounded after a failed arm, cleared by a code; 55 still set after the next good arm and disarm. Not used |

**With the mains off** (one read, 8 Oct 2026) the flags set were 29 *Bell SCB*, 32 *Detector Reset* and 67 *LED control*; 16 *Ready* and 25 *Force Armable* were clear. Three minutes later, still on battery, *Ready* was set again, so a zone active at the time of that read may explain it: check again with nothing moving.

### System flags

`Get system flags` (command 10) returns 8 bytes whose meaning isn't published. First clues from one panel (8 Oct 2026):

| State | Bytes |
|---|---|
| Normal | `00 20 09 00 00 00 00 01` |
| Once, after an alarm and before the engineer code (the keypad showed *Alarm Engineer Working On Site*) | byte 0 was `40`. A second alarm, disarmed with a user code, left it at `00`, so it isn't simply "after an alarm" |
| Mains off | byte 2 was `08` |
| Mains on, straight after the engineer code cleared *System Alerts!* | byte 2 was also `08`, so bit 0 of byte 2 isn't simply *mains OK* |
| A detector's cover open while disarmed, the tamper not logged (area flags 44 and 62 set, keypad *System Alerts!*) | byte 0 was `10` |

None of these is understood yet, and nothing relies on them. They're candidates for knowing what's already wrong when Home Assistant connects (D11): capture them with a tamper open, a fault, during an alarm, and before and after clearing *System Alerts!* (D13).

### Arming, alarms and the keypad

- **Every arm path works**: Away, Part Arm 1 and 2, switching mode, keypad arms (with the user number), and Apple Home through Home Assistant's HomeKit Bridge.
- **Switching mode** (disarm, then arm) really disarms for a moment: *disarmed* about 0.4 s after the request, then *in exit* 0.3 s later, with log 42 (*Remote Open/Close*) group 5 (*Open*) for the disarm. The driver hides it, so *disarmed* automations don't fire (`connect/panel.py`, `SWITCH_GRACE`).
- **A flag re-read just after a remote arm** can still show the exit flag; the driver ignores an exit flag on an armed area.
- **A flag re-read just after a disarm** can still show the area armed (8 Oct 2026: flags 22 *Full Armed* and 63 0.35 s after the panel announced the disarm from Home Assistant; caught up within 4.5 s). The driver ignores flags showing anything but disarmed for 5 s after a disarm message.
- **Exit times**: with the same programmed exit delay, a remote arm set after about 10 s and a keypad arm after 15 s (option 58 *Remote Arm Instant* on, which seems to shorten the exit time rather than skip it). The entry delay was 15–16 s.
- **Fail to set**: a zone active at the end of the exit time stops the arm and sounds the siren; log type 85 names each zone, and the keypad shows *Area arm fail*. With detectors only (no door contacts), keep moving in view of one until the exit time ends.
- **Zone alarms are logged twice** (again once reported). Sometimes only the second arrives, after the disarm. The zone's *alarmed* bit is set at once and stays set (alarm memory) until reset, so a zone in a disarmed area doesn't explain a later alarm.
- **After an alarm the panel can go back to *in entry*** when the entry zone is seen again: *in alarm*, *in entry* 1.6 s later, then *in alarm* again 16 s after that, with the sirens sounding throughout until the disarm. The zone stays *alarmed*. The driver keeps showing the alarm (`connect/panel.py`, `_apply_area`).
- **A detector that sees the entry route can raise a real alarm**: walking in, an interior PIR saw the person 0.6 s before the entry zone did, so the area went *in alarm* (keypad sounder only; the alarm was reported) and 0.6 s later *in entry*.
- **Part arms with nothing programmed** (14 Oct 2026, Part Arms 2 and 3 here) arm every zone, from the keypad and remotely (command 6). So a button mapped to an unprogrammed part arm can arm the whole house. Once, choosing a part arm at the keypad showed *Call Engineer* and didn't arm (nothing reached Home Assistant); later tries with both part arms armed normally, so the cause is unknown.
- **Which mode an exit delay is arming to** isn't in the messages: *in exit* and log 32 *Exit Started* are the same for Away and every part arm. The flags say (24 with 50–52 for a part arm, 63 for a full arm), so the driver re-reads them when an exit delay starts (up to 4 more times while they don't say) and shows the result as the alarm's `arming_to` (14 Oct 2026, keypad Part Arm 1, Part Arm 2 and Away).
- **Zones a part arm leaves out** report *auto bypass* (zone bit 6) from the start of its exit delay until the disarm (Part Arm 1 and the Landing, 14 Oct 2026).
- **Outputs** (14 Oct 2026): 16 goes from 255 to 240 while someone uses a keypad; 17 and 18 are 1 while disarmed, 0 while a keypad is in use or in the exit delay, and 2 once part armed (3 when fully armed on a tester's panel); 1, 2 and 4 are 8 while armed.
- **After an alarm or a tamper**, clearing *System Alerts!* at the keypad needed the engineer code on this panel (it shows as user 0 in the *user* message); the spanner light stayed on afterwards (probably a service reminder).

### Power

- **Readings**: volts = 13.7 + (reading − reference) × 0.07; current = reading × 9 mA.
- **On mains**: about 300 mA (and 18 mA into the battery) at about 13.6 V. **On battery**: both currents read 0, and the voltage falls (13.0 → 12.2 V in 2 minutes once; 13.6 → 12.2 V over 9 minutes, the battery 13.6 → 12.4 V, another time).
- **A single reading can be wrong:** with mains on, one read gave 0 mA at 13.14 V and the next, 30 s later, 333 mA at 13.63 V (12 Oct 2026, twice in a morning); testers on ComWiFi and ComIP saw false mains-off after comms drops. The integration needs two "on battery" readings in a row (about a minute) and ignores readings below 9 V. The panel's own *AC Fail* log entry still shows at once.
- The **AC Fail** log arrives at once, but the restore never does, so the driver decides mains is back from the readings (`connect/conditions.py`): within 30 s, as they're taken every 30 s (7 s on 8 Oct 2026). Panels that never report current are only called "on battery" below 12.9 V.

### Clock

- A **total power loss** (mains and battery) reset the clock to **31 Oct 2023**.
- The clock can hold an **impossible date** (day 0, month 0): treated as "not set".
- The panel keeps local wall-clock time; the integration uses Home Assistant's time zone, not the computer's (a Docker container on UTC would otherwise put the panel an hour out in summer).
- **The SmartCom can correct the clock itself, but only through the Texecom Cloud.** Texecom's SmartCom installation guide (INS760EN-8) says the cloud service uses an NTP server for synchronisation, and that setting a panel up for Texecom Connect programs its reporting numbers with ports that, among other things, correct the clock for the country of installation. Panels not set up for the cloud (like the test panel) get no correction: its clock was 7 s ahead on 12 Oct 2026 and 43 s behind on 14 Oct. Whether the cloud's correction still happens while Home Assistant holds the SmartCom's session is untested. Clock sync is on from 0.2.11 (new setups, and older ones that never turned it off): it only writes when the clock is more than a minute out, so it leaves a cloud-corrected clock alone.

## Seen on testers' panels

From diagnostics that users shared (their panels, not this one):

- **No area states 6–8 on V5.02** (Elite 24, V5.02.01, 14 Oct 2026): a Part Arm 2 at the keypad sent *part armed* (4) with log 79 *Part Arm 2*, and nothing after it; a full arm at the keypad sent *armed* (3) with log 43 *Quick Arm* (group 6). There the part arm comes from the log entry or a flag re-read.
- **The start of an exit delay doesn't say which mode is coming** (same panel; on this one [the flags do](#area-flags): 24 with 50–52, or 63): a Part Arm 2 and a full arm from the keypad both began with *in exit* (1) and log 32 *Exit Started* (group 16, parameter 11: keypad 1). Only the messages when the area armed said which.
- **Log type 127 and a short message `03 e0`** (same panel): when the entry/exit zone (zone 1) opened during the exit delay, the panel sent a two-byte message `03 e0` (type 3 is an *output* message, normally three bytes) and log 127 group 5 *Open*, parameter 1, areas 1 and 2; when the zone closed a second later, log 127 group 6 *Close*. Neither is in Texecom's published lists. Nothing relies on them; the diagnostics list them as unrecognised.
- **Output messages** (same panel): arming (full or part) set outputs 1, 2 and 4 to state 8, and 17 and 18 to state 3; disarming set them back to 0 and 1.
- **System flags' last byte** differs: `01` here (V6.05.03), `03` on an Elite 24 V6.06.03 and on the V5.02.01 panel, `00` on an Elite 64 V5.00.03. Meaning unknown.
- **ComWiFi** listens on port **10002** (Elite 24, V6.06.03).

## Seen by other projects

Reported by other Texecom projects on their panels, not yet seen on this one. Useful to know, and worth checking (sources in [Other projects](other-projects.md)):

- **Busy NAKs can last**: after a burst of events (~50 s of them), an Elite 88 (V6.02.02, ComIP) answered the keep-alive with a NAK on every retry. Not a dead session. The driver here treats a NAK as "busy, change nothing", so it doesn't reconnect.
- **One connection, enforced at TCP**: a second connection is refused outright while one is live. After a session ends, a ComIP accepted a new login after 2 s (this SmartCom: 10–70 s).
- **A client holding its own dead socket can't log in again** until that socket is closed. The driver closes a dropped session's socket straight away and keeps hold of the task that does it.
- **The alarm-time drop is the SmartCom's**: on a dedicated ComIP the session stayed up through an alarm. A SmartCom session can also carry modem commands (`ATH0`, `ATZ`) when the SmartCom reports.
- **An ARC set to Texecom Connect, with nothing behind it**: a texecom2mqtt user with an Elite 24 on V6.05.03 and a SmartCom (like this panel) saw occasional frames with bad CRCs, and logins answered with `A` (`0x41`, likely the start of one of those modem commands), while ARC 1's protocol was *Texecom Connect* though nothing used Texecom Cloud. They suspect the panel's attempts to report, and set every ARC's protocol to *Disabled* (engineer code → *UDL/Digi Options* → *Program Digi* → ARC 1–5) ([texecom2mqtt-hassio#125](https://github.com/dchesterton/texecom2mqtt-hassio/issues/125), Oct 2026). Only for a panel nobody monitors and that doesn't use Texecom Cloud. Whether the errors stopped, and whether it also ends this SmartCom's alarm-time drop, isn't known yet.
- **"Disarmed" not always sent**: after disarming Part Arm 2 on an Elite 88, the area message didn't always arrive. The keep-alive re-read corrects it here within 30 s.
- **Flag 0 as alarm memory**: on an Elite 48 (V4.02.01) flag 0 was clear while the alarm sounded and set after the disarm; flags 5, 28, 30, 44, 61 and 62 were the ones set while it sounded. If this panel did the same, a re-read after a keypad disarm would show *Alarm!* again (D15).
- **Arm and disarm "as a user"** (commands 29 and 30) apply that user's rights: an "Arm Only" user's code armed but couldn't disarm. Not used here.
- **Get user** (command 27, the user number as one byte) returns a user's name **and code** and settings: 23 bytes on a Premier Elite 24 (V6.05). The name is the first 8 bytes; the code follows as BCD, all `FF` for a user without one. A NAK past the last user (25 on a 24-user panel), and for user 0: the engineer code, which keypad events report as user 0, has no user record. The driver reads only the name (`decode_user_name`): once, the first time Home Assistant connects while no names are saved, and when asked to fill in the names in the options. On the owner's Elite 24 (V6.05) the 25 reads take about 9 s; the rest of the reply is never decoded, kept or logged, and a reply with a bad CRC isn't shown in the debug log.

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
- **The adapter takes the connection itself** (the panel is behind its serial port), so a connection opening says nothing about the panel: the driver counts it as connected only once the panel sends something on that connection. `ASTATUS` is sent on connect (after a UDL session, once the feed is due back). Through the ESP Easy serial server, the connection opened in 10–23 ms and the Elite 24 answered `"NN` 0.70–0.88 s after `ASTATUS` (three tries, 20 Oct 2026). A connection the panel never answers is a failed attempt: the back-off grows, and an outage logs one warning.

### Premier Elite 168, V6.06.03 (a tester's panel, Oct 2026)

Its COM port set to *Crestron System*, behind a serial-to-network adapter:

- **Neither command form above is accepted.** Seven attempts (armed and disarmed, with and without a Texecom Connect session open on the SmartCom's port) all went the same way: no `OK` to `\W<udl>/` within 8 s, `ERROR` to the text `\D<bitmask>/`, **NAK `03 0F ED` to the binary `D` frame**, then ACK `03 06 F6` to the logout `H` (so the session was open). The panel's event log recorded nothing for them.
- **Texecom Connect frames work inside the same kind of session.** Bare, a Connect *Login* gets no reply on this port; after `\W<udl>/` (with or without its `OK`) the same Login gets ACK (`74 52 07 00 01 06 e3`). A Connect client kept in such a session armed Part Arm 2 (`ArmArea`, in exit, then part armed after the 10 s exit delay) and disarmed (`DisarmArea`); the event log showed *Remote Command*, *Remote Part Arm 2* and *Remote Open/Close*.
- So the driver now tries, last, inside the same session: Connect *Login* (the UDL code), then Connect *ArmArea* (arm type = 0 full, or the part arm 1–3) or *DisarmArea*, with the area bitmap sized for the panel (2 bytes for 168 zones / 16 areas). The Connect replies (`t R len seq <command> <ACK|NAK> crc`) arrive on the same port, between text lines and binary frames. **Confirmed on that panel (18 Oct 2026)** with this driver, while another Connect client stayed logged in through the SmartCom: a disarm while already disarmed, a Part Arm 2 and a disarm all succeeded, each about 9 s from the request to the logout (the 8 s is the login's missing `OK`). The exchange: `ERROR`, NAK `03 0F ED`, Connect Login ACK `74 52 07 <seq> 01 06 <crc>`, Disarm ACK `74 52 07 <seq> 08 06 <crc>`, logout ACK `03 06 F6`. The panel logged *Remote Command*, *Remote Part Arm 2* and *Remote Open/Close*. After the arm, the held-back `"X0010` and `"A001204` (user 204 for a remote arm) arrived about 30 s later; after a disarm the driver shows *Off* at once and `"D0010` follows.
- **Power readings work the same way**: `\W<udl>/`, Connect *Login*, *GetSystemPower* (25), logout `H`. The driver reads them 2 minutes after connecting and then every 30 minutes, only while no area is arming, in entry or in alarm (a session might get in the way of someone at a keypad); a failed read waits for the next regular one, as every session holds back the text feed for ~30 s.
- **The stored event log can be read the same way** (Elite 168 V6.06.03, 18 Oct 2026, read-only probes in a short session): Connect **15 GetLogPointer** (no body) → 2 bytes, little-endian (715); **16 GetLogEvent** `[index lo][index hi][count]` → `count` entries of 9 bytes, **oldest first from `index`** (type, group, parameter, areas low, 4-byte timestamp, areas high — the live type-5 log message layout); **17 GetTextLogEvent** `[index lo][index hi]` → the keypad text of one entry, e.g. `13:11.35 18/10  A............... REMOTE Disarmed`. The newest entry is pointer − 1 (a 2000-entry ring on a 168). Reading 5 entries at once worked. The entries matched, to the second, what a Connect client received live (*Exit Started*, *Remote Command*, *Remote Part Arm 2*, *Remote Open/Close*); the panel's clock was ~4 s behind. UDL sessions, refused commands and these reads add **no** log entries. Layouts from shuuryou/texmond, where 16 and 17 are defined but not used.
- **A failed arm, read from the log** (Elite 168, 18 Oct 2026, a door contact left open, Part Arm 2 through Connect): *Exit Started*, then at the end of the 10 s exit delay *Interior* (zone 51), *Exit Error (Arming Failed)*, *Arm Failed* (parameter = the open zone, 94) and *Bell Active*, all in the same second. Read 20 s after the arm, about 7 s after a live Connect client saw it. The Crestron feed itself said only `"X0010`, and released that (held back) about 30 s after the log read's logout, with nothing after it, so the driver ignores a held-back `X` for an area whose failure it has just read.
- **The same Connect reads on a Premier Elite 24, V6.05.03** (19 Oct 2026, Crestron on COM3 through an ESP Easy serial server; this firmware accepts the text and UDL commands, so arming never needs Connect): after `\W<udl>/` a Connect *Login* also gets ACK, *GetPanelIdentification* (22) returns `Elite 24 ENG->SW V6.05.03LS1`, *GetSystemPower* works, and *GetLogPointer* returned 410 in two sessions, so these sessions add no log entries here either. **But the port stays in Connect mode for about 2¼ minutes after the logout** (`H` still gets ACK `03 06 F6`): `ASTATUS` gets NAK `03 0F ED`, and zone changes arrive as Connect messages (`74 4D 08 <seq> 01 <zone> <state> <crc>`) instead of `"Z` lines, until the held-back `"Z` lines arrive in one burst and `ASTATUS` answers again. A second session 34 s after the first one's logout still got Connect messages. So on this panel each session holds back the text feed for over 2 minutes, against ~30 s on the Elite 168 — why the regular readings are an option, off by default.
- **Armed, from the log** (Elite 168, 19 Oct 2026): with the after-arm read at 20 s, the second session pushed the held-back `"X`/`"A` out to ~52–54 s after the arm, against ~10 s with a Connect client on the SmartCom. The driver now reads at 12 s and shows the area armed as soon as the log has an unambiguous arm entry (*Part Armed*, *Quick Arm*, *(Quick/Remote) Part Arm 1–3*; not the *Open/Close* types, which disarms log too), then ignores the held-back `X` and `A` for it. Read-only sessions don't wait the full login time for an `OK` once the panel has answered Connect without one (it often doesn't send it).
- **An arm's held-back events can arrive after a later disarm** (Elite 168, 21 Oct 2026): armed from Home Assistant (armed, from the log, 24 s later), disarmed from it 19 s after that. The disarm's session came before the arm's held-back events were released, and 30 s after the disarm's logout the panel sent them all together: `"X0010`, `"A001204` 0.6 s later, `"D0010` 1.9 s after that. Too far apart to coalesce, and past the arm's own grace, so the area showed *Arming*, *Armed*, then *Off* again. The driver now ignores held-back `X` and `A` after its own disarm, until the next `D` or until the feed is due back plus the same grace.
- **Never hold a UDL session open** or resend `\W` inside one: a client that did (re-login every 60 s, binary Connect frames passing through, for about a day) had its bytes taken as programming frames (garbage on the keypad display), and its later `\W` got no reply, then `ERROR`.

## Still unknown

Things to find out on real panels (each is a task in the [live test plan](../testing/live-test-plan.md#part-d--mapping-the-protocol)):

- What **area states 6 and 7** mean exactly, and whether other values exist.
- **Output messages**: which outputs (bell, strobe, others) report, what their states mean, and what the two-byte `03 e0` is.
- **Log event types** not in the tables above, including **type 137** (observed during testing, meaning unknown) and **type 127** ([a tester's panel](#seen-on-testers-panels)).
- Whether a remote **arm is refused** (NAK) with a zone open or a fault present, and what the panel sends then.
- Commands not used yet that could help: **user names** (its reply includes the code), **zone bypass**, **outputs**, **the keypad text** (command 14).
- What the **system flags** (command 10) mean beyond the [first clues](#system-flags), and which **area flags** show tampers and faults that are already there when Home Assistant connects (D11–D13).
- Why the panel sometimes **doesn't log a tamper** (a limit on repeats?), and what else sets area flag 44 *Internal Alarm* while disarmed (chime, walk test, the fail-to-set warning, a 24-hour zone).
- Whether **Ready** (flag 16) is set exactly when the area can be armed, and what it shows while armed.
- The type numbers of the event-log entries [not yet seen live](#matched-against-the-panels-own-event-log), and what log 53's parameter means with a SmartCom or ComIP.
- The exact **refusal window** after a session closes, and what affects it.
- Why the SmartCom waited for the disarm before reporting an alarm (and dropping the session), and whether that depends on how the panel is set to report alarms.
- The SmartCom's **network name (DHCP hostname)**, which could let Home Assistant discover it automatically.
- Whether the panel changes its own clock for **summer time**, and how that interacts with clock sync (UK clocks change on 25 October 2026 and 28 March 2027).
- **Other panels and modules**: Elite 12, 48, 64, 88, 168, 640; firmware V4 and other V6 versions; ComIP; more than one area.

---

[← Testing](testing.md) · [Live test plan →](../testing/live-test-plan.md)
