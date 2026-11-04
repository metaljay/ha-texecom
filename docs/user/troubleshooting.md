# Troubleshooting

**On this page:** [Setting up](#setting-up) · [Day to day](#day-to-day) · [The panel clock is wrong](#the-panel-clock-is-wrong) · [The panel is unreachable](#the-panel-is-unreachable) · [Getting more detail](#getting-more-detail) · [Reporting a problem](#reporting-a-problem) · [Known limits](#known-limits)

## Setting up

| What you see | Try |
|---|---|
| **"Couldn't connect"** | Check the IP address, and that the port is **10001**. If the Texecom app, Homebridge or texecom2mqtt was connected a moment ago, wait a minute and try again: the SmartCom only lets one thing in at a time, and waits a while after the last one leaves |
| **"The panel refused the UDL code"** | The UDL code is the panel's remote-access code, not your keypad code. Try **1234** (Texecom's default); if that fails, ask your installer |
| **"The UDL code is 4 to 8 digits"** | Digits only, no spaces |
| **"Connected, but the panel reported no zones in use"** | Your installer may not have set the zones up yet, or this isn't the panel you expected |
| **"This panel is already set up"** | It's already in **Settings → Devices & services → Texecom** |
| **"Connecting to your panel…" for a long time** | Up to a minute is normal if something else was connected to the SmartCom just before |

## Day to day

| What you see | Try |
|---|---|
| **Everything shows as unavailable** | The panel has been unreachable for over 3 minutes. Look at **Panel connection** on the panel's device page and at **Settings → Repairs**, then see [The panel is unreachable](#the-panel-is-unreachable) |
| **Panel connection goes off for about 2 minutes after an alarm** | Normal: the SmartCom sends its own report of the alarm, then lets Home Assistant back in. The alarm and the zones keep their last state meanwhile, and Home Assistant reconnects by itself |
| **The SmartCom's address changed** | **Settings → Devices & services → Texecom → ⋮ → Reconfigure**, and enter the new one (leave the UDL code empty to keep it). Reserve the address in your router so it doesn't happen again |
| **"Alarm not set" notification** | A zone was still active when the exit time ended (a door open, or someone in view of a sensor), so the panel didn't arm. The notification names the zone. Close it or keep out of view, and arm again |
| **Home, Night or another button is missing from the alarm** | It's set to *'Not used'*: change it in **Configure → Arm buttons** |
| **Home or Night is missing in the Home app** | The Home app remembers an alarm's buttons: see [Apple Home](apple-home.md#changed-night-or-home) |
| **"Home Assistant isn't connected to the panel just now"** when arming | It reconnects by itself: wait for **Panel connection** (on the panel's device) to come back, usually within a minute, and try again |
| **"The panel refused"** when arming | Look at the keypad, which usually says what needs attention (a fault, a tamper, an open zone) |
| **"The panel didn't accept the request"** | The message says why. If it keeps happening, [report it](#reporting-a-problem) |
| **"Alarm panel on battery" notification** | The panel has had no mains power for 2 minutes: check for a power cut, and the panel's fused spur. The alarm still works on its battery for now. The notification goes within half a minute of the mains coming back |
| **"Alarm tamper" notification** | It says where: *the panel's lid is open*, *a detector's cover is open*, the bell box… Put it right and it goes by itself. *Auxiliary Tamper* means one of the detectors on the shared tamper circuit, so check each one's cover. A zone's name (*Patio Door*, say) means that detector's cover is open or its wiring is damaged. *Internal Alarm* means the panel set off its internal sounders while the alarm was off but didn't report why (a panel has been seen not to report a detector's open cover): the keypad says what it is, and entering a code there silences it and clears the notice |
| **Keypad users show as *User 3*** | The panel has no name for that user (or Home Assistant couldn't read the names when it first connected). Give them names in **Configure → Names for keypad users**, or tick *Fill in the names stored in the panel* there |
| **Ready to arm says *Not ready*** | Its *open zones* (in the sensor's attributes) list what's open: close it, or keep out of the sensor's view. If nothing is listed, look at the keypad |
| **A zone your installer removed is still listed** | Once Home Assistant has read the zones again (**Configure → Read zones and areas from the panel again**), open the old zone's device and use **⋮ → Delete** |
| **A zone shows the wrong icon or wording** | Open it, then **⚙️ Settings → Show as** |
| **The alarm takes about a minute to come back after restarting Home Assistant** | Normal: the SmartCom waits a while before it lets a new connection in |
| **Keypad says "System Alerts!" after an alarm** | The panel wants the alarm acknowledged at the keypad. On some panels this needs the engineer code |

## The panel clock is wrong

If the panel loses all power (mains and battery), its clock resets (on one panel, to 31 October 2023). Its event log, and any timed arming your installer set up, use that clock.

With **Keep the panel clock right** on (you choose it at setup, or later under Options → Panel clock), Home Assistant sets the clock itself, within a minute, when it connects and once a day. With it off, Home Assistant still checks the clock when it connects: if it's more than 5 minutes out, **Settings → Repairs** shows *"Your alarm panel's clock is wrong"*. Click it, then **Submit**: this turns on **Keep the panel clock right**, which sets the clock now and checks it once a day, using Home Assistant's time zone.

## "Your alarm panel said something new"

This notice in **Settings → Repairs** means the panel sent a message the integration doesn't recognise yet. That's usually a detector, module or firmware nobody has been able to test. Nothing is wrong, and everything else keeps working. To help add support for it, download the diagnostics and select **Learn more** to share them; or select **Ignore** and it won't ask again. Texecom Connect only.

## The panel is unreachable

If Home Assistant can't reach the panel for 15 minutes, **Settings → Repairs** says so, with the address it's trying and the last error. It clears itself once the panel is back.

Check:

1. The SmartCom has power and its network light is on.
2. Its address hasn't changed (look in your router). If it has, use **Reconfigure** (above).
3. Nothing else is connected to it: the Texecom app, Homebridge, texecom2mqtt, or a second Home Assistant.

**Crestron:** the notice also comes when the adapter is reachable but the panel says nothing (*the connection opens, but the panel isn't answering*): check the panel has power, the cable between the adapter and the panel, the adapter's serial speed, and that the COM port is still set to *Crestron System*.

Just after an alarm, a drop is normal: the SmartCom closes the connection to report the alarm and turns Home Assistant away for about two minutes. The log says *the panel closed the connection after the alarm*, and the alarm's entities keep their state meanwhile.

## Getting more detail

For a detailed log, the easy way: **Settings → Devices & services → Texecom Premier Elite → ⋮ → Enable debug logging**, make the problem happen, then **⋮ → Disable debug logging**, and your browser downloads the log.

To capture start-up too, add this to `configuration.yaml` and restart Home Assistant instead:

```yaml
logger:
  logs:
    custom_components.texecom: debug
```

Then look in **Settings → System → Logs** (**Show raw logs**) for lines with *Connect* or *Crestron*. Remove it again afterwards: it's chatty.

**Diagnostics:** on the panel's device page, **⋮ → Download diagnostics** gives a report of the panel's state, including the status flags it reports, the last 1,000 messages it sent and a count of each kind (with the times, so they show when the alarm was set and unset), with the codes, address and keypad user names removed. Attach it to bug reports.

## Reporting a problem

1. Download the diagnostics (above).
2. [Report it on GitHub](https://github.com/metaljay/ha-texecom/issues/new?template=2-bug.yml): what you did, what you expected, what happened, and a rough time.
3. Check the log you paste for anything private first.

Questions rather than bugs: [Discussions](https://github.com/metaljay/ha-texecom/discussions).

Your panel has something Home Assistant doesn't show yet (wireless detectors, temperatures, other modules)? [Share your panel's data](https://github.com/metaljay/ha-texecom/issues/new?template=3-data.yml) (the form says what helps), or see [Helping improve it](../../CONTRIBUTING.md).

## Known limits

What the panel does and doesn't tell Home Assistant, found by testing on a real Premier Elite 24 (V6.05.03):

- **One connection at a time.** The SmartCom serves Home Assistant *or* the Texecom app (or Homebridge, texecom2mqtt). It also refuses a new connection for up to about a minute after the last one closed, so after Home Assistant restarts the alarm can take a minute to come back.
- **After an alarm** the SmartCom drops Home Assistant for about 2 minutes to send its own report (on one panel, just after the disarm). Entities keep their last state for up to 3 minutes; Home Assistant reconnects and catches up by itself. If your SmartCom also reports to a monitoring centre, consider a second module (a ComIP) for Home Assistant.
- **Mains coming back** isn't reported by the panel, only the failure. Home Assistant reads the panel's power every 30 seconds instead, so *Mains power* turns back on within half a minute.
- **Which detector was tampered with** usually isn't known: most installs wire every detector's tamper switch to one shared circuit, which the panel reports as *Auxiliary Tamper*.
- **Keypad lights** (e.g. the spanner) aren't sent; **Keypad display** shows the screen text instead.
- **Ready to arm** isn't announced by the panel: Home Assistant asks a moment after zones change while the alarm is off (and every 30 seconds).
- **Tampers and faults that started before Home Assistant connected** (for example during a restart) show only once the panel next reports them: the panel tells Home Assistant when they start and end, and there's no way yet to ask what's open now. Mains power is the exception: it's worked out from the power readings within half a minute. And if the panel's internal sounders are still going (until a code is entered), **Tamper** shows *Internal Alarm*. After a dropped connection (rather than a restart), Home Assistant reads what the panel logged meanwhile, so a tamper closed or a fault ended during the drop clears once it's back (Texecom Connect, on Premier Elite 24, 48, 88 and 168 panels).
- **The panel doesn't always report a tamper.** One panel didn't report a detector's cover opening (the third time that day), only its closing. Home Assistant then goes by the panel's internal alarm: **Tamper** shows *Internal Alarm* while the alarm is off and the internal sounders have been set off with no tamper or alarm reported.
- **The panel's clock resets** if it loses all power (see [above](#the-panel-clock-is-wrong)).
- **Zones active at the end of an exit time** make the arm fail, and the panel sounds its "fail to set" warning (see *Alarm not set* above).

---

[← Automations](automations.md) · [Back to the README](../../README.md)
