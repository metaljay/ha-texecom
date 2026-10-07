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
| **Setup takes a long time** | Up to a minute is normal if something else was connected to the SmartCom just before |

## Day to day

| What you see | Try |
|---|---|
| **Everything shows as unavailable** | The panel has been unreachable for over 3 minutes. Look at **Panel connection** on the panel's device page and at **Settings → Repairs**, then see [The panel is unreachable](#the-panel-is-unreachable) |
| **The SmartCom's address changed** | **Settings → Devices & services → Texecom → ⋮ → Reconfigure**, and enter the new one. Reserve the address in your router so it doesn't happen again |
| **"Alarm not set" notification** | A zone was still active when the exit time ended (a door open, or someone in view of a sensor), so the panel didn't arm. The notification names the zone. Close it or keep out of view, and arm again |
| **Home or Night is missing from the alarm** | It's set to *'Not used'*: change it in **Configure** |
| **Home or Night is missing in the Home app** | The Home app remembers an alarm's buttons: see [Apple Home](apple-home.md#changed-night-or-home) |
| **"The panel didn't accept the request"** when arming | The message says why. *Not connected*: wait for **Panel connection** to come back. *Refused*: look at the keypad, which usually says what needs attention |
| **A zone shows the wrong icon or wording** | Open it, then **⚙️ Settings → Show as** |
| **The alarm takes about a minute to come back after restarting Home Assistant** | Normal: the SmartCom waits a while before it lets a new connection in |
| **Keypad says "System Alerts!" after an alarm** | The panel wants the alarm acknowledged at the keypad. On some panels this needs the engineer code |

## The panel clock is wrong

If the panel loses all power (mains and battery), its clock resets (on one panel, to 31 October 2023). Its event log, and any timed arming your installer set up, use that clock.

Home Assistant checks the clock when it connects. If it's more than 5 minutes out, **Settings → Repairs** shows *"Your alarm panel's clock is wrong"*. Click it, then **Submit**: this turns on **Keep the panel clock right**, which sets the clock now and checks it once a day, using Home Assistant's time zone.

## The panel is unreachable

If Home Assistant can't reach the panel for 15 minutes, **Settings → Repairs** says so, with the address it's trying and the last error. It clears itself once the panel is back.

Check:

1. The SmartCom has power and its network light is on.
2. Its address hasn't changed (look in your router). If it has, use **Reconfigure** (above).
3. Nothing else is connected to it: the Texecom app, Homebridge, texecom2mqtt, or a second Home Assistant.

## Getting more detail

For a detailed log, add this to `configuration.yaml` and restart Home Assistant:

```yaml
logger:
  logs:
    custom_components.texecom: debug
```

Then look in **Settings → System → Logs** (**Show raw logs**) for lines with *Connect* or *Crestron*. Remove it again afterwards: it's chatty.

**Diagnostics:** on the panel's device page, **⋮ → Download diagnostics** gives a report of the panel's state with the codes and address removed. Attach it to bug reports.

## Reporting a problem

1. Download the diagnostics (above).
2. [Report it on GitHub](https://github.com/metaljay/ha-texecom/issues/new?template=2-bug.yml): what you did, what you expected, what happened, and a rough time.
3. Check the log you paste for anything private first.

Questions rather than bugs: [Discussions](https://github.com/metaljay/ha-texecom/discussions).

## Known limits

What the panel does and doesn't tell Home Assistant, found by testing on a real Premier Elite 24 (V6.05.03):

- **One connection at a time.** The SmartCom serves Home Assistant *or* the Texecom app (or Homebridge, texecom2mqtt). It also refuses a new connection for up to about a minute after the last one closed, so after Home Assistant restarts the alarm can take a minute to come back.
- **During an alarm** the SmartCom drops Home Assistant for about a minute to send its own report. Entities keep their last state for up to 3 minutes; Home Assistant reconnects and catches up by itself. If your SmartCom also reports to a monitoring centre, consider a second module (a ComIP) for Home Assistant.
- **Mains coming back** isn't reported by the panel, only the failure. Home Assistant reads the panel's power every 30 seconds instead, so *Mains power* turns back on within half a minute.
- **Which detector was tampered with** usually isn't known: most installs wire every detector's tamper switch to one shared circuit, which the panel reports as *Auxiliary Tamper*.
- **Keypad lights** (e.g. the spanner) aren't sent; **Keypad display** shows the screen text instead.
- **The panel's clock resets** if it loses all power (see [above](#the-panel-clock-is-wrong)).
- **Zones active at the end of an exit time** make the arm fail, and the panel sounds its "fail to set" warning (see *Alarm not set* above).

---

[← Automations](automations.md) · [Back to the README](../../README.md)
