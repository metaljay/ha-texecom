<img src="https://raw.githubusercontent.com/metaljay/ha-texecom/main/docs/images/icon.png" width="96" align="right" alt="">

# Texecom Premier Elite for Home Assistant

Control your Texecom **Premier Elite** alarm from **Home Assistant**, using the **SmartCom** you already have. No MQTT broker, no add-on, no YAML: install it from HACS, type your SmartCom's address, and your zones are read from the panel.

[![Open your Home Assistant and add this repository to HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=metaljay&repository=ha-texecom&category=integration)

> **Unofficial.** Not made or supported by Texecom. Tested on a Premier Elite 24 (firmware V6.05.03) with a SmartCom. **Tried it on another panel? [Tell us how it went](https://github.com/metaljay/ha-texecom/issues/new?template=1-tested.yml)**, even if it all worked. The protocol work is shared with the [Homebridge plugin fork](https://github.com/metaljay/homebridge-texecom).

## ✨ What you get

<img src="https://raw.githubusercontent.com/metaljay/ha-texecom/main/docs/images/dashboard.png" width="760" alt="The Alarm dashboard: the alarm with Away, Night, Home and Off buttons, recent activity, panel health, and every zone grouped by type">

- 🛡️ **An alarm for each area**: arm **Away**, **Night** or **Home**, disarm, and see when it's arming, in its entry delay or going off. Changes made at the keypad show straight away.
- 🚪 **A sensor for every zone**, named as on your panel: doors and windows, motion detectors, smoke and gas. Each zone is its own device, so you can put it in a room.
- 📋 **An Alarm dashboard**, made for you at the end of setup from Home Assistant's own cards. Nothing else to install.
- 🔋 **Panel health**: connection, mains and battery voltage, tampers, and what the keypad screen says.
- ⚡ **Automations**: lights on when the hallway sees movement, a notification when the alarm goes off, arm when everyone leaves.
- 🍏 **Apple Home** too, through Home Assistant's built-in HomeKit Bridge: see [Apple Home](#apple-home).

## 🧰 What you need

| | |
|---|---|
| 🏠 | A Texecom **Premier Elite** panel with a **SmartCom** (the box the Texecom app uses) or a ComIP |
| 🔢 | The panel's **UDL code**. Texecom's default is **1234**, and most panels keep it |
| 🏡 | **Home Assistant** 2025.3 or newer, with [HACS](https://hacs.xyz) |

> ℹ️ **About the Texecom app.** Home Assistant uses the same SmartCom connection as the Texecom app, so the app can't connect while Home Assistant is. Your alarm works exactly as before, the app's alarm notifications still reach your phone, and Home Assistant does everything the app does day to day. If Homebridge or texecom2mqtt already use your SmartCom, turn them off first.

## 🚀 Set it up

### 1️⃣ Find your SmartCom's address

Open your router's list of connected devices and find the SmartCom. Note its **IP address** (something like `192.168.1.50`), and reserve that address in the router so it never changes.

### 2️⃣ Install from HACS

Click the **Add to HACS** button above (or in HACS: **⋮ → Custom repositories**, add `https://github.com/metaljay/ha-texecom` as an **Integration**). Then search HACS for **Texecom Premier Elite**, click **Download**, and **restart Home Assistant**.

### 3️⃣ Add the integration

[![Open your Home Assistant and start setting up Texecom](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=texecom)

Or go to **Settings → Devices & services → Add integration** and search for **Texecom**.

<img src="https://raw.githubusercontent.com/metaljay/ha-texecom/main/docs/images/setup-1-choose.png" width="420" alt="Choose Texecom Connect"> 

Choose **Texecom Connect**. (Crestron is for COM ports set to Crestron System: see [below](#crestron).)

<img src="https://raw.githubusercontent.com/metaljay/ha-texecom/main/docs/images/setup-2-smartcom.png" width="420" alt="SmartCom address, port and UDL code">

Enter the SmartCom's **IP address**, leave the **port** at **10001**, and enter the **UDL code**. Home Assistant logs in and reads your panel's areas and zones. That takes about ten seconds, or up to a minute if something else was connected to the SmartCom just before.

### 4️⃣ Choose your arm modes

<img src="https://raw.githubusercontent.com/metaljay/ha-texecom/main/docs/images/setup-3-arm-modes.png" width="420" alt="Found your panel: choose the part arms for Night and Home">

Home Assistant shows what it found. **'Away'** always arms the whole alarm. Choose which **part arm** sits behind **'Night'** and **'Home'** (or *'Not used'* to hide that button). See [Know your part arms](#part-arms) if you're unsure.

Your **area** (usually one, named by your installer, e.g. *House*) becomes the **alarm**: a device called *House alarm*. The panel itself appears as *Premier Elite 24 panel*, and each zone as its own device.

Leave **Add an Alarm dashboard to the sidebar** ticked, and click **Submit**. That's it. 🎉

### 5️⃣ Finish off

- 🏷️ **Rooms**: zones whose names match a room you already have (for example *Kitchen*, or *Lounge* if your *Living Room* has *Lounge* as an alias) go in that room automatically. Put the others in a room from their device page.
- 🔁 **Sensor types**: doors, windows, smoke and gas are guessed from the zone's name and type; everything else shows as motion. To change one, open it and use **Settings → Show as**.
- 🔔 **Notifications**: see [Automations](#automations).

<a id="part-arms"></a>

## 🗺️ Know your part arms

Besides a full arm (**Away**), your panel can have up to three **part arms**, each arming only some zones. Your installer chose them, so they differ from home to home:

| Part arm | Might arm | Good Home Assistant button |
|---|---|---|
| Part Arm 1 | Downstairs only, so you can move around upstairs at night | **Night** |
| Part Arm 2 | Just the garage, or the doors and windows | **Home** |
| Part Arm 3 | Often not set up | (none) |

**Find out what yours cover** (ask your installer, or look at the part-arm settings in Wintex), then give Night and Home only the part arms you actually use. An unused part arm arms nothing useful, or the wrong zones.

When the panel is part armed from the keypad, Home Assistant shows the mode you matched to that part arm. A part arm you didn't match shows as Home.

## 🧭 Using it

<img src="https://raw.githubusercontent.com/metaljay/ha-texecom/main/docs/images/security.png" width="760" alt="Home Assistant's Security page showing the alarm, doors and smoke detector">

- **States** read *Off (disarmed)*, *Arming…*, *Armed: Away* / *Night* / *Home*, *Entry delay* and *Alarm!*.
- **The Alarm dashboard** (in the sidebar) has the arm buttons, the last day's activity, panel health and every zone. Edit it like any dashboard, or rebuild it from **Configure → Create or refresh the Alarm dashboard**.
- **Home Assistant's own Overview** picks the alarm up as well: its **Security** summary lists the alarm, doors and smoke detectors, and each room shows its zones.
- **Apple Home**: see [Apple Home](#apple-home).

### ⚙️ Options

<img src="https://raw.githubusercontent.com/metaljay/ha-texecom/main/docs/images/options.png" width="420" alt="Texecom options">

Open **Settings → Devices & services → Texecom → Configure**:

| Option | What it does |
|---|---|
| **Night uses / Home uses** | The part arm behind each button |
| **Home Assistant alarm code** | Optional. Home Assistant asks for it before disarming (and arming, if you tick the next option). It's checked by Home Assistant, not the panel, and has nothing to do with your keypad codes |
| **Create or refresh the Alarm dashboard** | Builds the dashboard again from the current zones |
| **Keep the panel clock right** | Once a day (and on connecting), sets the panel's clock if it's more than a minute out. Uses Home Assistant's time zone, so British Summer Time is handled. If it's off and the clock is more than 5 minutes out (e.g. after a total power loss), Home Assistant's **Repairs** shows a notice with a **Fix** button |
| **Read zones and areas from the panel again** | After your installer changes zones or names. This also happens by itself when engineer programming ends |

To change the SmartCom's address or the UDL code, use **⋮ → Reconfigure**.

<a id="apple-home"></a>

## 🍏 Apple Home

Home Assistant's built-in **HomeKit Bridge** puts the alarm (and any zones you like) in the Home app, with Siri, Control Centre and Apple's critical alerts.

1. Go to **Settings → Devices & services → Add integration → HomeKit Bridge**, choose **Alarm Control Panel** (and **Binary Sensor** if you want the zones), and follow the pairing steps. If you already have a HomeKit Bridge, open its **Configure** and add `alarm_control_panel.texecom_house` instead.
2. In the Home app, put the alarm in a room and turn on its notifications.

**What you'll see**

| In the Home app | Comes from |
|---|---|
| **Away**, **Night**, **Home**, **Off** buttons | Away always; Night and Home only if you gave them a part arm. Set them up *before* pairing: the Home app remembers an alarm's buttons, and after changing them you need to remove the alarm from the bridge and add it again |
| **Arming…** | The exit delay. The alarm's page shows *Off* until the panel has armed, because that's what it is until then |
| **Triggered**, with a critical alert | The alarm going off |
| Arms and disarms made at the keypad | Show straight away (an arm after its exit delay) |

**Codes:** the Home app can't ask for a code. If you set a Home Assistant alarm code, HomeKit Bridge needs it in its settings (`entity_config` → `code`) or arming from the Home app fails. Without a code, the Home app arms and disarms as it did with Homebridge, so consider [arming automatically from Home Assistant](#automations) and leaving disarming to the keypad.

<a id="automations"></a>

## ⚡ Automations

The alarm and zones work in any automation.

### 🚗 Arm automatically when everyone leaves

This is the most useful one, and Home Assistant does it more safely than Apple Home can. Home Assistant can **arm** on its own when the house empties, and leave **disarming** to you: at the keypad as usual, or in Home Assistant with the optional alarm code (**Configure → Home Assistant alarm code**). Apple Home has no code entry for an alarm, so a "disarm when I arrive" automation there trusts your phone's location alone. A glitch, or a stolen unlocked phone, would open the house.

1. Go to **Settings → Automations & scenes → Create automation → Create new automation**.
2. Open **⋮ → Edit in YAML**, paste the example below, and change the entity names to yours.
3. **Save** it as *Alarm: arm when everyone leaves*.

```yaml
alias: "Alarm: arm when everyone leaves"
description: Sets the alarm to Away when nobody has been home for 5 minutes.
triggers:
  - trigger: numeric_state
    entity_id: zone.home        # how many people are home (needs the companion app)
    below: 1
    for: { minutes: 5 }
conditions:
  - condition: state
    entity_id: alarm_control_panel.texecom_house
    state: disarmed
actions:
  - action: alarm_control_panel.alarm_arm_away
    target:
      entity_id: alarm_control_panel.texecom_house
  - action: notify.notify       # or persistent_notification.create
    data:
      message: "Everyone's out, so the alarm is set to Away."
mode: single
```

- **Who's home?** The example uses `zone.home`, which counts the people whose phones run the Home Assistant app. If you track presence another way (for example an *Occupancy* toggle that Apple Home switches when the last person leaves), use that instead: `trigger: state`, `entity_id: input_boolean.occupancy`, `to: "off"`.
- **The 5 minutes** stop a quick trip to the bins from arming the house. The exit delay still runs as usual.
- **If someone is still inside**, the arm fails at the end of the exit time (the panel sounds its "fail to set" warning) and Home Assistant fires an `arm_failed` event naming the zone. Add an automation on that event to tell you.
- **We suggest not adding an automatic disarm.** If you do, require something besides phone location (for example the front door being unlocked with a code).

### 📣 Events

The integration also fires a `texecom_event` for things that aren't states:

| `type` | When | Data |
|---|---|---|
| `zone_alarm` | A zone set the alarm off (Connect) | `zone`, `zone_name`, `tamper` |
| `user` | Someone entered a code or tag at a keypad | `user`, `method` |
| `fault` / `fault_cleared` | A fault such as *AC Fail* (mains off), *Low Battery* or *Fail to Communicate* | `source`, `log_type` |
| `tamper` | A tamper that isn't a zone (Connect), e.g. *Panel Box Tamper* (the lid) or *Auxiliary Tamper* (a detector on the shared tamper circuit) | `source`, `log_type` |
| `arm_failed` | Arming failed because a zone was active when the exit time ended (one event per zone). The panel sounds its "fail to set" warning | `zone`, `zone_name`, `areas` |

<details>
<summary>Example: tell everyone which zone set the alarm off</summary>

```yaml
triggers:
  - trigger: event
    event_type: texecom_event
    event_data:
      type: zone_alarm
actions:
  - action: notify.notify
    data:
      title: "🚨 Alarm"
      message: "Set off by {{ trigger.event.data.zone_name }}"
```

</details>

## 💡 Good to know

- 🚨 **When the alarm goes off**, the panel briefly drops the connection to send its own alarm report through the SmartCom. Home Assistant reconnects and catches up within seconds. If your SmartCom also reports to a monitoring centre, consider a second module (a ComIP) for Home Assistant.
- 🔁 **Reconnects by itself** after a power cut, a router restart or a dropped connection, and reads the panel's state again so nothing is missed. After Home Assistant restarts, the SmartCom can take about a minute to accept it again.
- 🔐 **The UDL code is stored in Home Assistant** (like any integration password). Anyone with admin access to your Home Assistant can arm and disarm, so keep that access tight, and consider the optional alarm code.
- 🔌 **Mains and faults**: **Mains power** turns off as soon as the panel reports a mains failure, and **Problem** lists any fault it reports. The panel reports a mains failure straight away; see [Known limits](#known-limits) for when the mains comes back.
- 🔧 **Tampers**: the panel's **Tamper** sensor turns on while its lid, a keypad, the bell box or a detector is open, and says which. Most installs wire every detector's tamper to one shared circuit, so the panel (and Home Assistant) can't say *which* detector; it shows as *Auxiliary Tamper*. Each zone also has a hidden **Tamper** sensor, which only works if that zone's tamper is wired to the zone itself.
- 🧾 **Diagnostics**: on the panel's device page, **Download diagnostics** gives a report with the codes and address removed, for bug reports.

<details>
<summary><b>🛠️ Troubleshooting</b></summary>

| What you see | Try |
|---|---|
| "Couldn't connect" while setting up | Check the address and that the port is 10001. If the Texecom app, Homebridge or texecom2mqtt was connected a moment ago, wait a minute and try again |
| "The panel refused the UDL code" | Try 1234; if that fails, ask your installer |
| Everything shows as unavailable | Look at **Panel connection** on the panel's device page, and at **Settings → System → Logs** for "Connect:" lines |
| A zone shows the wrong icon or wording | Open it, then **Settings → Show as** |
| Home or Night is missing from the alarm | It's set to *Not used*: change it in **Configure** |

For detail, add this to `configuration.yaml` and restart:

```yaml
logger:
  logs:
    custom_components.texecom: debug
```

</details>

<details>
<summary><b>📋 Prefer to build the dashboard yourself?</b></summary>

The ready-made dashboard uses only built-in cards. If your Home Assistant can't create it (the option shows an error), add a dashboard, open **⋮ → Raw configuration editor**, and paste this, changing the entity names to yours:

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

</details>

<a id="known-limits"></a>

<details>
<summary><b>📏 Known limits</b> (what the panel does and doesn't tell Home Assistant)</summary>

Found while testing on a real Premier Elite 24 (V6.05.03):

- **One connection at a time.** The SmartCom serves Home Assistant *or* the Texecom app (or Homebridge, texecom2mqtt). It also refuses a new connection for about a minute after the last one closed, so after Home Assistant restarts the alarm can take a minute to come back.
- **During an alarm** the SmartCom drops Home Assistant for about a minute to send its own report. Home Assistant reconnects and catches up by itself.
- **Mains coming back** isn't reported by the panel, only the failure. Home Assistant reads the panel's power every 30 seconds instead, so *Mains power* turns back on within half a minute.
- **Which detector was tampered with** usually isn't known: most installs wire every detector's tamper switch to one shared circuit, which the panel reports as *Auxiliary Tamper*.
- **Keypad lights** (e.g. the spanner) aren't sent; the *Keypad display* sensor shows the screen text instead.
- **The panel's clock resets** if it loses all power (mains and battery). Home Assistant raises a Repairs notice and can set it for you.
- **Zones that saw you at the end of an exit time** make the arm fail, and the panel sounds its "fail to set" warning. Home Assistant reports it as an `arm_failed` event naming the zone.

</details>

<a id="crestron"></a>

<details>
<summary><b>🔁 Using a COM port set to Crestron instead?</b></summary>

Choose **Crestron** when adding the integration if one of the panel's COM ports is set to **Crestron System** and you reach it through a serial-to-network adapter (or a SmartCom/ComIP switched to Crestron), or a USB-serial cable on the Home Assistant machine. It's useful for keeping the Texecom app on its SmartCom while Home Assistant uses a spare COM port.

How it differs from Texecom Connect:

- Zone names can't be read: you enter how many zones and areas you have, they appear as *Zone 1*, *Zone 2*… and you rename them in Home Assistant.
- Only active/clear is reported (no tamper), and there are no voltage readings.
- An arm from the keypad doesn't say whether it was full or part: choose how it shows (**When armed at the keypad, show as**).
- Part arms work for area 1 only.
- After arming or disarming from Home Assistant, the panel holds back its other updates for about 30 seconds. Nothing is lost, but sensors update late.
- The UDL code is only needed for arming and disarming; without it you get sensors only.

To set a COM port to Crestron at the keypad: engineer code → *UDL/Digi Options* → 8 *Com Port Setup* → choose the port → *No* to edit → 8 *Crestron System* → *Yes* to save.

</details>

<details>
<summary><b>🧪 Coming from the Homebridge plugin or texecom2mqtt?</b></summary>

- Disable the Homebridge Texecom plugin (or stop texecom2mqtt) first: they can't share the SmartCom.
- Nothing needs copying across: zones, names and areas come from the panel. Choose Night and Home part arms as you had them.
- For the Home app, expose the alarm through Home Assistant's HomeKit Bridge. It appears as a new accessory, so set its room and notifications again.
- If you armed and disarmed from Apple Home automations (for example a dummy switch that follows who's home), move the arming into Home Assistant (see [Arm automatically when everyone leaves](#automations)) and delete the Apple Home ones, so the two don't fight.

</details>

## 🙏 Credits

The Texecom Connect protocol support follows the publicly released [texecom-connect](https://github.com/davidMbrooke/texecom-connect) by Joseph Heenan (Apache-2.0), and arming and the zone/area reads are adapted from [texecom2mqtt](https://github.com/dchesterton/texecom2mqtt-hassio) by Daniel Chesterton (MIT). The Crestron support grew out of the Homebridge plugin by Kieran Jones, max-christian and [Chris Posthumus](https://github.com/K1LL3R234/homebridge-texecom) (MIT). Full details are in [NOTICE](NOTICE).

Texecom, Premier Elite, SmartCom, ComIP and Wintex are trademarks of Texecom Ltd. This project is not affiliated with Texecom.
