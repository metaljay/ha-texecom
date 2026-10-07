# Other projects, and what we took from them

Other Texecom projects and other Home Assistant alarm integrations, checked in October 2026: what each offers, what this integration took, and what could come next. Read this before researching again, and add to it when you look at something new.

**On this page:** [Licences](#licences) · [Texecom projects](#texecom-projects) · [Home Assistant alarm integrations](#home-assistant-alarm-integrations) · [Home Assistant's quality scale](#home-assistants-quality-scale) · [Ideas not done yet](#ideas-not-done-yet)

## Licences

This integration is MIT licensed. From another project we may:

- **use facts** (command numbers, flag and log names, how a panel behaves) from anywhere;
- **adapt code** only from MIT or Apache-2.0 projects, crediting them in [NOTICE](../../NOTICE);
- take **nothing but ideas** from GPL or AGPL code.

## Texecom projects

| Project | What it is | Licence | What we took | Could take later |
|---|---|---|---|---|
| [davidMbrooke/texecom-connect](https://github.com/davidMbrooke/texecom-connect) | The original Texecom Connect implementation (Python, 2018) | Apache-2.0 | Framing, CRC, commands and message layouts | — |
| [Sjoerdfc/texecom-connect](https://github.com/Sjoerdfc/texecom-connect) | A fork that publishes to Home Assistant over MQTT | Apache-2.0 | The idle-time state re-read (our keep-alive) | — |
| [southseaboy/texecom-connect](https://github.com/southseaboy/texecom-connect) | A fork, live-tested in 2026, with tests | Apache-2.0 | Area flags 17–19 and 24; single-flag reads for V4 firmware; **the names of all 73 area flags, the log types and groups, and the "get system flags" command** | Which log `parameter`s are users and which are zones; telling when an alarm is *over* from the "live" flags (5, 28, 30, 44, 61, 62) |
| [michaelmarconi/texecom_alarm](https://github.com/michaelmarconi/texecom_alarm) | A Home Assistant add-on over MQTT, aimed at a dedicated ComIP, thoroughly documented (2026) | MIT | NAKs after bursts of events; area states 6 and 7; the names of log types 204–209; **a client holding its own dead socket can't log in again** (we now keep hold of the task that closes it) | Its other findings are in [What we know about the panel](protocol.md#seen-by-other-projects) |
| [dchesterton/texecom2mqtt-hassio](https://github.com/dchesterton/texecom2mqtt-hassio) | The best-known bridge: MQTT and a Home Assistant add-on | MIT (the app itself ships as an image) | Arm, disarm and reset, and the zone and area reads | **Setting the keypad text** (command 14); offering Part Arm 3 as Home Assistant's *Vacation* or *Custom bypass* mode |
| [shuuryou/texmond](https://github.com/shuuryou/texmond) | An event-monitoring daemon (C#, serial) | AGPL-3.0 | Nothing: ideas only | — |
| [JumpMaster/TexecomManager](https://github.com/JumpMaster/TexecomManager), [shuckc/pytexalarm](https://github.com/shuckc/pytexalarm), [RoganDawes/pialarm](https://github.com/RoganDawes/pialarm), [RoganDawes/ESPHome_Wintex](https://github.com/RoganDawes/ESPHome_Wintex), [ricol99/casa](https://github.com/ricol99/casa), [GoosieZA/esphome-texecom](https://github.com/GoosieZA/esphome-texecom), [Prinsessen/openhab-texecom-bridge](https://github.com/Prinsessen/openhab-texecom-bridge), [garethflowers/homebridge-texecom-connect](https://github.com/garethflowers/homebridge-texecom-connect) | Crestron, Wintex/UDL over serial, ESPHome and openHAB bridges | Mostly MIT | The Crestron lines, the UDL session and its binary arm frames (via the [Homebridge plugin fork's review](https://github.com/metaljay/homebridge-texecom/blob/master/docs/MAINTAINER-REVIEW.md)) | Reading the panel's event log over UDL |
| Texecom Connect API help (a PDF on Crestron's application market) | Texecom's own command and flag reference | Texecom's | — (not reachable from where this was researched) | The meaning of the 8 system flags; confirming the flag names |

## Home Assistant alarm integrations

What other alarm integrations offer, against this one. The core integrations checked: Bosch Alarm, Elk-M1, Risco, Satel Integra, Ness, Envisalink, Total Connect, AlarmDecoder and Yale. The custom ones: Alarmo.

| Feature | Who has it | Here |
|---|---|---|
| Ready to arm | Bosch (*ready to arm away / home* sensors), Alarmo, AlarmDecoder (an attribute) | ✅ **Ready to arm** for each area, from the panel's own flag, with the open zones |
| Faults and troubles as sensors | Bosch, Risco, Yale | ✅ Problem, Mains power, Tamper, and notifications |
| Phone notifications with a button | Alarmo (actionable notifications) | ✅ The *ask to set the alarm when everyone leaves* blueprint |
| Who armed or disarmed, by name | Alarmo (its own users) | ✅ Names for keypad users |
| Which zone set it off / stopped the arm | Alarmo (`open_sensors`) | ✅ *changed by* the zone; the *Alarm not set* notification names the zones |
| Events in the activity list | Elk-M1 | ✅ |
| Set the panel clock | Bosch, Risco, Elk-M1 (as actions) | ✅ Automatically (an option, and a Repairs fix) |
| Diagnostics, Repairs, reconfigure | Bosch, Total Connect, Satel | ✅ |
| Zone bypass | Risco (switches), Total Connect (buttons), Elk-M1 | ❌ No bypass command is known in Texecom Connect |
| Outputs as switches | Satel, Elk-M1 | ❌ Texecom Connect reports outputs; no command to switch them is known |
| A message on the keypads | Elk-M1; texecom2mqtt | ❌ Possible (command 14): an idea below |
| Arm instantly, vacation, custom bypass | Elk-M1, Total Connect, Alarmo | ❌ Texecom has full arm and three part arms; Part Arm 3 could be a third button |
| Exit and entry countdown | Alarmo (a `delay` attribute its card uses) | ❌ An idea below: the panel gives its delays |

## Home Assistant's quality scale

Home Assistant grades core integrations by its [quality scale](https://developers.home-assistant.io/docs/core/integration-quality-scale/). It doesn't apply to custom integrations, but it's a good checklist. Where this integration stands:

- **Bronze, met but one.** Setup screens (which check the connection), unique IDs, entity names, `runtime_data`, and one entry per panel are done. By design, starting Home Assistant doesn't wait for the panel (that can take a minute), so the entities show as unavailable until it connects.
- **Silver, met but one.** Error messages, unloading, availability, quiet logging while unreachable, reauthentication, and tests are all done. The one gap is `PARALLEL_UPDATES`: commands are serialised in the driver anyway, so setting it would change nothing.
- **Gold, met but discovery.** Diagnostics, reconfiguration, Repairs, translations and icons, entity categories and disabled-by-default entities, re-reading zones, deleting old zone devices, and the docs are done. **Discovery** isn't possible: the SmartCom's network identity is generic.

## Ideas not done yet

Each needs evidence from a real panel, or a decision from the owner, first. The [live test plan](../testing/live-test-plan.md) has the tests.

| Idea | Why | Needs |
|---|---|---|
| Read the tampers and faults that are already there on connecting | Fixes the known limit after a restart | Which flag or system flag shows them (D11–D13) |
| A "siren sounding" sensor (flags 28–30) | Know when the bell sounds and stops | A test during an alarm (E6). Over a SmartCom, Home Assistant is disconnected during an alarm anyway |
| A message on the keypads (command 14) | e.g. "Back door open" on the keypads | The owner's agreement, and a supervised test |
| User names read from the panel (command 27) | Names without typing them | Its reply includes each user's **code**: a decision on handling codes (the docs promise keypad codes aren't stored) |
| Catch up on the event log after a reconnect (commands 15 and the log read) | Events missed while disconnected, e.g. during the alarm drop | Tests of what those commands return |
| Exit and entry countdown attributes | Dashboards could show a countdown | Small: the area details already include the delays |
| Part Arm 3 as a third button | Panels that use all three part arms | An option, shown as *Vacation* or *Custom bypass* |
| Remote access (log 53/54), engineer programming (58) and keypad lockout (69) in the activity list | Know when someone accessed the panel | Check whether Home Assistant's own login writes log 53 (D14) |
| An "alarm over" state from the live flags | Show when the siren stops, as southseaboy does | D15 first: whether flag 0 is alarm memory on V6 |

---

[← What we know about the panel](protocol.md) · [Developer guide](README.md)
