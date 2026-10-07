# Live test plan

A plan for testing on a **real panel**, with three goals:

1. **Find bugs** the simulated panels can't show.
2. **Improve the experience** for someone setting it up and living with it.
3. **Map the protocol**: write down everything the panel does, so the integration can rely on it (results go in [What we know about the panel](../development/protocol.md)).

It's written for an AI agent working on the owner's own machine, with the owner there for anything marked 🔴. Each test has an ID (A1, C4…) so findings can refer to it.

**On this page:** [Safety rules](#safety-rules-read-first) · [Before you start](#before-you-start) · [Recording findings](#recording-findings) · [A: Regression](#part-a--regression-on-the-simulated-panel) · [B: New features](#part-b--new-features) · [C: Real panel, read-only](#part-c--real-panel-read-only) · [D: Mapping the protocol](#part-d--mapping-the-protocol) · [E: Supervised tests](#part-e--supervised-tests-on-the-real-panel) · [F: Everyday use](#part-f--everyday-use-cases) · [G: First-time user](#part-g--first-time-user-walkthrough) · [H: Fringe cases](#part-h--fringe-cases) · [Afterwards](#afterwards)

## Safety rules (read first)

| Mark | Means | Rule |
|---|---|---|
| 🟢 | Read-only, or the simulated panel | Fine any time |
| 🟠 | Changes something on the panel but doesn't arm it or sound anything (e.g. setting its clock) | Ask the owner first |
| 🔴 | Arms, disarms, sounds the alarm, cuts power, or opens a tamper | **Only with the owner there and agreeing to that test.** The owner disarms |

1. **Never arm or disarm the real panel on your own.** If a test arms it, the owner is present, agrees first, and disarms it at the keypad.
2. **Alarm tests are loud and may be reported.** Before any test that can sound the alarm (E6, E8, E9): agree a time with the owner, warn anyone in the house and the neighbours, and if the alarm is monitored (an alarm receiving centre, or keyholders who get alerts) tell them or put it in test mode first.
3. **One connection to the SmartCom.** Before connecting a test Home Assistant to the real panel, disable the Texecom entry on the everyday Home Assistant (**Settings → Devices & services → Texecom → ⋮ → Disable**). Re-enable it when you finish. Never leave two connected.
4. **Codes stay secret.** Read the UDL code from where the owner keeps it; never write it, or any keypad code, into a file, log excerpt, issue or commit. If a reply from the panel contains codes, discard it and only note its format.
5. **Don't change the panel's programming.** Wintex is for reading. Back up Home Assistant's configuration before changing it.
6. **Stop and ask** if anything unexpected happens: a fault you didn't cause, *System Alerts!*, a sounder, the panel not answering.

## Before you start

You need:

- A **test Home Assistant** (separate from the everyday one), with the branch under test copied into its `custom_components/texecom`, and **debug logging** on:

  ```yaml
  logger:
    logs:
      custom_components.texecom: debug
  ```
- The **simulated panel** (`python tests/fake_connect_panel.py 10001 --demo`) running somewhere the test Home Assistant can reach.
- For C–E and H: the real panel's **SmartCom** (and, for Crestron tests, its serial-to-network bridge), and the owner's private notes for addresses and access.
- A **clock**: write down the time of every step, to the second where it matters. Logs are matched by time.

## Recording findings

Keep a findings log (a file outside the repository while it may contain anything private). One row per finding:

| ID | Test | When | What I did | Expected | Saw | Log (no codes) | Kind | Suggested fix (compartment) |
|---|---|---|---|---|---|---|---|---|
| F1 | E2 | 2026-10-12 19:04:10 | Armed Night, then Away from the dashboard | *Arming…*, then *Armed: Away* | *Off* for a second | `Connect: area 1 …` | Bug | `connect/panel.py` `_apply_area` |

**Kind** is one of: *Bug* (wrong behaviour), *UX* (works but confusing or slow), *Protocol* (something new about the panel), *Idea*. For each bug, say which compartment the fix belongs in ([Making changes](../development/making-changes.md#where-does-my-change-go)).

## Part A — Regression on the simulated panel

🟢 Everything here uses the simulated panel. It checks that nothing a user relies on has changed.

| ID | Do | Expect |
|---|---|---|
| A1 | Add the integration: **Texecom Connect**, the simulated panel's address, port 10001, UDL 1234; arm modes Night = Part Arm 1, Home = Not used; dashboard ticked | Found 8 zones and 1 area (*House*); the entry is created; **Alarm** appears in the sidebar |
| A2 | Look at the devices | *Premier Elite 24 panel*, *House Alarm*, one device per zone; zones in matching rooms |
| A3 | Look at the entities | `alarm_control_panel.texecom_house` (Off); `binary_sensor.texecom_front_door` (door); smoke detector shows as smoke; Panel connection on; Mains power on; Keypad display shows *Premier Elite*; voltages around 13.7 V |
| A4 | Arm Away, Night, switch Night → Away, disarm, from the dashboard | *Arming…* then the armed state; a mode switch never shows *Off* |
| A5 | **Configure → Home Assistant alarm code**: set one; disarm without it, with a wrong one, with the right one | Refused, refused, disarmed |
| A6 | **Configure → Night and Home buttons**: change Home to Part Arm 2 | The alarm gains a Home button after it reconnects |
| A7 | **Configure → Read zones and areas from the panel again** | *Reading the panel…*, then a message with the number of zones and areas; zones unchanged |
| A8 | **Configure → Create or refresh the Alarm dashboard** | It asks first, then says the dashboard is ready; the dashboard is rebuilt |
| A9 | **⋮ → Reconfigure** with the same details | *Connecting to your panel…* while the entry pauses; it comes back |
| A10 | **Download diagnostics** | No UDL code, alarm code or address in the file |
| A11 | Restart Home Assistant | The alarm comes back by itself |
| A12 | Restart the simulated panel with `--clock-reset` | **Repairs** shows *Your alarm panel's clock is wrong*; its **Fix** turns on clock sync and the notice goes |
| A13 | Stop the simulated panel for 16 minutes | Panel connection off at once; entities keep their state for 3 minutes, then unavailable; **Repairs** says it's unreachable after 15 minutes; all clears when it's back |
| A14 | Expose the alarm through the test instance's HomeKit Bridge, if paired to a test iPhone | Away/Night/Off buttons; arming from the Home app works |
| A15 | Delete the entry and add it again | Same entity IDs as before |

## Part B — New features

🟢 Checks for features added since the last release, on the simulated panel started with `--commands` (see [Testing](../development/testing.md#the-simulated-panels)), so you can make it report tampers, mains failures, keypad users and alarms. "Send `lid open`" means type that command to it. Each new feature adds its checks here.

| ID | Do | Expect |
|---|---|---|
| B1 | Add the integration (as A1) and watch the screen after **Submit**. Follow the *setup guide* and *part arms explained* links | *Connecting to your panel…* with a short explanation, then the arm-modes screen. Both links open the right page |
| B2 | Add it with UDL code 9999, then 1234 | *Connecting…*, then back to the form saying the code was refused, with the address still filled in; 1234 goes through |
| B3 | Add it with an address where nothing answers | *Connecting…*, then *Couldn't connect*. Note how long it took |
| B4 | Open **Configure**, and follow the link at the top | A menu: *Night and Home buttons*, *Home Assistant alarm code*, *Names for keypad users*, *Notifications*, *Panel clock*, *Read zones and areas from the panel again*, *Create or refresh the Alarm dashboard*. The link opens the options guide |
| B5 | **Configure → Home Assistant alarm code**: set one; watch **Panel connection** and the simulated panel's output | It applies at once without reconnecting (Panel connection stays on); disarming now asks for the code |
| B6 | **Configure → Names for keypad users**: type `Sam is user three`; then `1 = Alex` and `3 = Sam` on two lines. Send `user 3`, then `area armed`; then `user 4`, `area off` | The first is refused with an example; the second saves without reconnecting. *Changed by* says *Sam*, then *User 4* |
| B7 | Send `mains off`; wait; send `mains on` | Within seconds: **Mains power** off, **Problem** shows *AC Fail*, and an *Alarm panel on battery* notification saying what to check. After `mains on`, all clear within about 30 seconds |
| B8 | Send `lid open`, `aux open`, then `lid closed`, `aux closed` | One *Alarm tamper* notification, updated to list both with what to check; gone once both are closed. **Tamper** follows |
| B9 | **Configure → Notifications**: turn both off; repeat B7 and B8 | No notifications; the sensors still change. Turn them back on |
| B10 | Send `armfail 4` | *Alarm not set* naming Kitchen |
| B11 | Send `area armed`, then `area alarm` and `zone 4 alarm`; then `area off`, `zone 4 closed` | *Alarm!*, changed by *Kitchen* |
| B12 | Open the alarm's page and the **Logbook** after B6–B11 | Plain sentences for each: *keypad used by Sam (a code)*, *reported a fault: AC Fail*, *fault cleared…*, *reported a tamper: Panel Box Tamper*, *tamper put right…*, *didn't arm: Kitchen was active…*, *was set off by Kitchen* |
| B13 | Send `refuse arm`, then arm from the dashboard. Then stop the simulated panel and arm again | *The panel refused…* (says to look at the keypad); then *Home Assistant isn't connected to the panel just now…*. Neither is a raw error |
| B14 | Import both blueprints ([Automations](../user/automations.md); the buttons point at `main`, so before merging use the branch's file URL in **Blueprints → Import blueprint**). Make *arm when everyone leaves* with an *Occupancy* toggle and 1 minute, and *tell me about the alarm* with a test phone | Turning the toggle off arms Away after a minute; turning it back on within the minute doesn't arm. The phone gets a notification for B7, B8, B10 and B11, with the texts in the guide |
| B15 | **⋮ → Reconfigure**: an address where nothing answers, then close the dialog during *Connecting…* | The entry comes back with its old address, Panel connection on within a minute |
| B16 | Look at the icons | **Mains power** off: a crossed-out plug; **Keypad display**: a keypad; battery voltage: a battery |
| B17 | On a Crestron entry (if you have one): open **Configure** | *Checking the panel* instead of *Notifications*, *Panel clock* and *Read zones…*; the activity list shows keypad users only |

## Part C — Real panel, read-only

🟢 Nothing here arms the panel or changes it. Disable the everyday entry first (safety rule 3).

| ID | Do | Record |
|---|---|---|
| C1 | Add the integration on the test Home Assistant against the real SmartCom. Time it from **Submit** to the arm-modes screen | Seconds taken; whether a "busy" retry happened (debug log); the zones and areas found, compared with Wintex |
| C2 | Open each door and window, walk past each detector, one at a time | Each zone sensor turns on and off within a second or two; the right device class (door/window/motion) |
| C3 | Watch **Keypad display** for an hour; note what the keypads show at the same time | Every text seen; whether the clock is always stripped |
| C4 | Look at the voltages and currents (enable the current sensors) | Values on mains; how much they move over an hour |
| C5 | **Download diagnostics** | The panel clock and its drift; the panel's info |
| C6 | Restart the test Home Assistant three times, a few minutes apart | How long until the alarm is back each time (the SmartCom's refusal window). Read the log for refused logins |
| C7 | Connect Wintex through the SmartCom while Home Assistant is connected, then disconnect it | Does Home Assistant stay connected? Anything odd in its log? |
| C8 | Try to open the Texecom app while Home Assistant is connected | What the app shows; whether Home Assistant's session survives |
| C9 | Leave it connected for 24 hours | Reconnects (count `logged in` lines), errors, warnings; memory use of the test instance |
| C10 | Crestron: add a Crestron entry over the network (the bridge), **without a UDL code**, zone and area counts as the panel has | Zones update when walked past; the alarm shows its state with no arm buttons; no warnings in the log at each status poll |

## Part D — Mapping the protocol

Most of this is listening: run it **alongside** Parts C and E, with debug logging on, and turn each scenario's log into a table. 🟢 unless it says otherwise.

| ID | Find out | How |
|---|---|---|
| D1 | **Every message the panel sends, by scenario** | During each test in C and E, collect the `Connect: message {...}` lines. Make a table: scenario → messages in order (kind, area/zone, state code, log type, group, parameter) |
| D2 | **Area states 6 and 7** | During E2 and E3 (part arms from Home Assistant and from the keypad), note exactly when state 6 or 7 arrives and what the area flags say just after |
| D3 | **Output messages** | During exit, entry, an alarm and a bell test (E-tests), record every `output` message: location and state. Which output is the bell, the strobe, the keypad sounder? |
| D4 | **Unknown log types** (including 137) | Any `log` message whose type isn't in [protocol.md](../development/protocol.md#log-event-types-the-integration-uses): when it came, and what had just happened |
| D5 | **The refusal window** | With the everyday entry disabled and the test entry disabled too: a small script (using `connect/client.py`) that logs in, logs out, and then tries again every 2 s until accepted. Repeat 20 times; record the waits. Login and logout only |
| D6 | **Read-only commands not used yet** | From the texecom-connect command list, try read-only commands such as reading the event log, output states and user *names*, with a small script using `ConnectClient.command()`. Record which answer and what the replies look like. **Never** keep a reply that contains codes. Don't send anything that writes, arms, bypasses or resets |
| D7 | 🔴 **When does the panel refuse an arm?** | With the owner: arm Away from Home Assistant with a door open; arm with a fault showing (during E9). Does the panel refuse at once (a NAK), or start the exit delay and fail to set? What messages arrive? |
| D8 | **The SmartCom's network name** | In the router's or the Home Assistant host's DHCP leases: the SmartCom's hostname and MAC. If the hostname is distinctive, Home Assistant could discover it by itself |
| D9 | **Crestron lines** | During C10 and (🔴, with the owner) an arm and disarm from Home Assistant over Crestron: every line, how long the held-back burst takes after the UDL session, and how often the UDL login gets an `OK` |
| D10 | **Summer time** | On the night the clocks change (UK: 25 October 2026, then 28 March 2027): read the panel clock (diagnostics) before 01:00 and after 02:00 GMT, once with **Keep the panel clock right** on and once off. Does the panel change its own clock? Does clock sync fight it? |
| D11 | **Asking what's open now** | Tampers and faults are only known from log events, so after a restart an open cover or a fault isn't shown until it changes. Look for a read-only command that reports the current system tampers and faults (system flags, or reading the last few log entries on connecting) with `ConnectClient.command()`, while E8 has a cover open |

Write every confirmed result into [protocol.md](../development/protocol.md), with the panel, firmware and date. Add a simulated-panel test for anything the code should now rely on.

## Part E — Supervised tests on the real panel

🔴 **Every test here needs the owner present and agreeing**, and the owner disarms. Do them in one session, in this order, with debug logging on and D1 running.

| ID | Do | Expect |
|---|---|---|
| E1 | Arm **Away** from the dashboard; the owner disarms at the keypad after it has armed | *Arming…* for the exit delay, then *Armed: Away* (changed by Home Assistant); *Off* after the keypad disarm (changed by the keypad user, by name if [B6](#part-b--new-features) names were set) |
| E2 | Arm **Night** from Home Assistant; once armed, switch to **Away** from Home Assistant; owner disarms | *Armed: Night*, then *Arming…*, then *Armed: Away*; **never** *Off* in between |
| E3 | Owner arms at the keypad: full, then part arm 1, then the part arm Home Assistant doesn't use | Each shows straight away with the user number; the unmatched part arm shows as *Home* |
| E4 | Owner arms Away, walks back in through the entry route, disarms during the entry delay | *Entry delay*, then *Off* |
| E5 | Arm Away from Home Assistant with a door left open | At the end of the exit time: the panel's fail-to-set warning, and an **Alarm not set** notification naming the door; it clears when the alarm next arms. The activity list says *didn't arm* |
| E6 | **Full alarm** (warn everyone first, safety rule 2): arm Away, let the entry delay run out | *Alarm!* naming the zone; Panel connection drops for about a minute while the SmartCom reports, but the alarm keeps showing *Alarm!*; the owner resets and disarms; *System Alerts!* on the keypad |
| E7 | During E1–E6, with the alarm in the Home app: arm from the Home app and with Siri; watch the alert during E6 | Buttons as configured; a critical alert for the alarm |
| E8 | Owner opens a detector's cover, then (if they're happy to) the panel lid, while disarmed. **This can sound the internal sounders** | **Tamper** on, with *Auxiliary Tamper* / *Panel Box Tamper*; off again when closed; `tamper` and `tamper_cleared` events; the *Alarm tamper* notification says where to look, and goes when closed |
| E9 | Owner switches off the panel's mains (its fused spur) for 5 minutes, then on. The battery must be healthy | **Mains power** off within seconds and **Problem** shows *AC Fail*; voltages fall; the *Alarm panel on battery* notification shows; all back within 30 s of the power returning |
| E10 | Restart the test Home Assistant during an exit delay, and (with E6) during an alarm. Also once during E8 with the cover still open | It comes back showing the right state. Record whether **Tamper** shows the open cover after the restart (it's expected not to: see D11) |
| E11 | Unplug the SmartCom's network cable for 2 minutes, then (separately) for 16 | Entities keep their state for 3 minutes; Panel connection off; **Repairs** after 15 minutes; everything back within a minute or two of reconnecting |
| E12 | The installer enters and leaves engineer programming (no changes) | Zones and areas are read again by themselves |

Don't cut **all** power (mains and battery) to the panel: it resets the panel's clock, which is already known. Only do it if the owner wants to check the clock repair end to end.

## Part F — Everyday use cases

Walk through these as the household would, end to end, on the everyday Home Assistant once the tested version is installed there. 🔴 where they arm. For each, note anything slow, confusing or missing.

| ID | Situation | Check |
|---|---|---|
| F1 | **Everyone leaves** | The *arm when everyone leaves* blueprint arms Away after the delay, only if disarmed, and (with *Then also*) tells someone |
| F2 | **Coming home** | Entry delay shows; disarming at the keypad shows who; nothing confusing in the activity list |
| F3 | **Going to bed** | Night from the Home app or Siri; morning disarm at the keypad |
| F4 | **Someone stays in** while others go out | Home or Night as the household would use them; no accidental Away |
| F5 | **Away for a week** | Mains, battery and connection stay healthy; a power cut or a dropped connection would be noticed |
| F6 | **The alarm goes off while you're out** | The *tell me about the alarm* blueprint's phone alert says which zone; the dashboard's activity list shows what happened and when |
| F7 | **A power cut** | The *Alarm panel on battery* notification (and the phone, with the blueprint); the battery voltage is visible; it clears when power returns |
| F8 | **The installer adds a zone** | It appears (re-read by itself, or with **Read zones and areas again**); the dashboard can be refreshed |
| F9 | **Home Assistant is down** for an update | The alarm itself carries on; Home Assistant catches up when it's back |

## Part G — First-time user walkthrough

🟢 Pretend you've never seen it, on the simulated panel or the real one (read-only). Follow only the README and what's on screen, and note every place you'd get stuck.

1. Install from HACS using only the README's buttons.
2. Add the integration; read every screen's words. Is it clear what a UDL code is? What a part arm is? What happens next?
3. Make each error happen and read the message: a wrong address, a wrong UDL code, a UDL code with a letter in it, the SmartCom busy (connect right after a restart).
4. Open **Configure** and read every entry in the menu and every screen. Would you know what each does without the docs?
5. Look at the devices, the dashboard and the Security summary on a phone as well as a computer.
6. Read the Repairs notices and the notifications (A12, A13, B7, B8, E5). Would you know what to do?
7. Import a blueprint from the [Automations](../user/automations.md) page as a newcomer would.
8. Time the whole setup.

Write each confusing word or missing step as a *UX* finding, with better wording.

## Part H — Fringe cases

| ID | Do | Look for | Mark |
|---|---|---|---|
| H1 | Enable the Texecom entry on **both** Home Assistants for 5 minutes | No crash; the one that loses keeps retrying quietly, and says so in Repairs after 15 minutes; all good once one is disabled | 🟢 |
| H2 | **Reconfigure** with a wrong UDL code, then a wrong address | An error on the form; the entry carries on with the old details | 🟢 |
| H3 | Reboot the router | Reconnects by itself | 🟢 |
| H4 | Power-cycle the SmartCom (not the panel) | Reconnects by itself; how long it takes | 🟢 |
| H5 | Change an option during an exit delay | The reload's reconnect doesn't lose the arm; the state is right afterwards | 🔴 |
| H6 | A zone bypassed at the keypad | The zone shows `bypassed: true` | 🔴 (owner at the keypad) |
| H7 | A keypad user with a tag instead of a code | The `user` event says `tag` | 🔴 |
| H8 | Walk through several zones quickly | No missed changes; no "busy" errors left showing | 🟢 |
| H9 | Back up and restore Home Assistant | The integration comes back unchanged | 🟢 |
| H10 | Leave it running for a week | Count reconnects, warnings and errors in the log | 🟢 |

## Afterwards

1. Re-enable the everyday Texecom entry; remove the test entries; turn debug logging off.
2. Turn the findings log into:
   - bugs and UX changes, each with the compartment it belongs in (the owner picks which to do; each becomes its own change);
   - new panel knowledge, written into [protocol.md](../development/protocol.md) without anything private;
   - new simulated-panel behaviour and tests for anything the code should now rely on.
3. Make changes following [AGENTS.md](../../AGENTS.md).

---

[← What we know about the panel](../development/protocol.md) · [Developer guide](../development/README.md)
