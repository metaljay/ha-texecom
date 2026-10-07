# Using it

**On this page:** [What you'll see](#what-youll-see) · [The alarm](#the-alarm) · [The Alarm dashboard](#the-alarm-dashboard) · [Options](#options) · [Codes](#codes) · [Good to know](#good-to-know) · [Build the dashboard yourself](#build-the-dashboard-yourself)

## What you'll see

| Device | What it has |
|---|---|
| **House Alarm** (one per area, named after it) | The alarm: arm and disarm, and its state |
| **Premier Elite 24 panel** (your panel's size) | **Panel connection**, **Mains power**, **Problem**, **Tamper**, **Keypad display**, battery and panel voltages (current readings are there too, switched off until you want them) |
| One device per zone, e.g. **Front Door** | Whether the zone is open or sees movement. Each also has a **Tamper** sensor, switched off until you want it |

Entity IDs start with `texecom_`, e.g. `alarm_control_panel.texecom_house` and `binary_sensor.texecom_front_door`, so they're easy to find. They don't change if you rename things in Home Assistant.

<img src="../images/security.png" width="760" alt="Home Assistant's Security page showing the alarm, doors and smoke detector">

Home Assistant's own **Overview** picks the alarm up as well: its **Security** summary lists the alarm, doors and smoke detectors, and each room shows its zones.

## The alarm

| State | Means |
|---|---|
| **Off (disarmed)** | Not armed |
| **Arming…** | The exit delay: leave now |
| **Armed: Away** | Fully armed |
| **Armed: Night** / **Armed: Home** | Part armed (see [Part arms explained](setup.md#part-arms-explained)) |
| **Entry delay** | Someone came in: disarm now |
| **Alarm!** | The alarm is going off |

- **Changed by** (on the alarm's details) says who or what did it: *Home Assistant*, a keypad user (*User 3*), or for an alarm, the zone that set it off (*Kitchen*).
- **Arms and disarms at the keypad** show straight away.
- **Switching mode** (for example Night to Away) shows *Arming…* while the panel switches over; you won't see *Off* in between, so automations that run on *Off* don't fire.

## The Alarm dashboard

<img src="../images/dashboard.png" width="760" alt="The Alarm dashboard: the alarm with Away, Night, Home and Off buttons, recent activity, panel health, and every zone grouped by type">

If you left the box ticked during setup, **Alarm** is in the sidebar: the arm buttons, the last day's activity, panel health and every zone grouped by type. It's built from Home Assistant's own cards, so you can edit it like any dashboard.

To build it again (for example after adding zones), use **Configure → Create or refresh the Alarm dashboard**. That replaces any changes you made to it.

## Options

<img src="../images/options.png" width="420" alt="Texecom options">

Open **Settings → Devices & services → Texecom → ⚙️ Configure**:

| Option | What it does |
|---|---|
| **'Night' uses / 'Home' uses** | The part arm behind each button. *'Not used'* hides the button |
| **Home Assistant alarm code** | Optional. Home Assistant asks for it before disarming. It's checked by Home Assistant, not the panel, and has nothing to do with your keypad codes |
| **Ask for the code when arming too** | Asks for that code when arming as well |
| **Keep the panel clock right** | Once a day (and on connecting), sets the panel's clock if it's more than a minute out, in Home Assistant's time zone (so British Summer Time is handled) |
| **Read zones and areas from the panel again** | After your installer adds or renames zones. This also happens by itself when engineer programming ends |
| **Create or refresh the Alarm dashboard** | Builds the dashboard again from the current zones |

To change the SmartCom's address or the UDL code, use **⋮ → Reconfigure** on the same page.

> Saving options reconnects to the panel. The SmartCom can take up to a minute to let Home Assistant back in, so the alarm may show as unavailable briefly.

## Codes

- **The UDL code** lets Home Assistant talk to the panel. It's stored in Home Assistant like any integration's password. Anyone with admin access to your Home Assistant can arm and disarm, so keep that access tight.
- **The Home Assistant alarm code** (optional, in Options) is an extra check on top: Home Assistant asks for it before disarming.
- **Your keypad codes** aren't used or stored by Home Assistant.

## Good to know

- 🚨 **When the alarm goes off**, the panel drops the connection for about a minute to send its own alarm report through the SmartCom. The alarm and zones **keep showing their last state** (e.g. *Alarm!*) meanwhile, so dashboards and the Home app don't go blank; **Panel connection** shows the link itself. Home Assistant reconnects and catches up by itself.
- 🔁 **It reconnects by itself** after a power cut, a router restart or a dropped connection, and reads the panel's state again so nothing is missed. After Home Assistant restarts, the SmartCom can take about a minute to let it back in.
- 🔌 **Mains power** turns off as soon as the panel reports a mains failure, and **Problem** lists any fault the panel reports (e.g. *AC Fail*, *Low Battery*). The panel doesn't report the mains coming back, so Home Assistant checks the power readings every 30 seconds instead.
- 🔧 **Tamper** turns on while the panel's lid, a keypad, the bell box or a detector is open, and says which. Most installs wire every detector's tamper switch to one shared circuit, so it can't say *which* detector: that shows as *Auxiliary Tamper*.
- 🕐 **The panel clock** resets if the panel loses all power (mains and battery). Home Assistant notices and offers to fix it (see [Troubleshooting](troubleshooting.md#the-panel-clock-is-wrong)).
- 📟 **Keypad display** shows what the keypads say (e.g. *System Alerts!*), without the clock.

## Build the dashboard yourself

The ready-made dashboard only uses built-in cards. If your Home Assistant can't create it (the option shows an error), add a dashboard, open **⋮ → Raw configuration editor**, and paste this, changing the entity names to yours:

```yaml
views:
  - title: Alarm
    type: sections
    sections:
      - type: grid
        cards:
          - type: heading
            heading: Alarm
            icon: mdi:shield-home
          - type: tile
            entity: alarm_control_panel.texecom_house
            vertical: false
            features_position: bottom
            features:
              - type: alarm-modes
                modes: [armed_away, armed_night, disarmed]
            grid_options:
              columns: full
          - type: logbook
            target:
              entity_id: alarm_control_panel.texecom_house
            hours_to_show: 24
      - type: grid
        cards:
          - type: heading
            heading: Zones
            icon: mdi:motion-sensor
          - type: tile
            entity: binary_sensor.texecom_front_door
          - type: tile
            entity: binary_sensor.texecom_hallway
```

---

[← Setting it up](setup.md) · [Apple Home →](apple-home.md)
