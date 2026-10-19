# Using a COM port set to Crestron

Most people should use **Texecom Connect** with a SmartCom or ComIP ([Setting it up](setup.md)). Crestron is the other way in: one of the panel's COM ports set to **Crestron System**, reached through a serial-to-network adapter (or a SmartCom/ComIP switched to Crestron), or a USB-serial cable on the Home Assistant machine.

It's useful for keeping the Texecom app on its SmartCom while Home Assistant uses a spare COM port.

## How it differs from Texecom Connect

- **Zone names can't be read.** You enter how many zones and areas you have; they appear as *Zone 1*, *Zone 2*… and you rename them in Home Assistant.
- **Only open/closed (active/clear)** is reported for zones: no tamper, mains or keypad display. **With the UDL code, you can switch on power and event-log readings** (⚙️ → **Power and event-log readings**, off by default): every 30 minutes Home Assistant reads the panel and battery voltages and currents, and any new entries in the panel's event log (each shows in the activity list, *panel log: Remote Part Arm 2*, and is a `texecom_event` of type `log`). Each reading holds back the panel's other updates for a while (about 30 seconds on an Elite 168, over 2 minutes on an Elite 24); none is taken while an area is arming, in entry or in alarm. **About 20 seconds after Home Assistant arms** through Texecom Connect, the log is read whether or not the readings are on, so an arm that failed (a door left open) shows *Alarm not set* and the alarm goes back to *Off* instead of staying on *Arming*.
- **An arm at the keypad doesn't say whether it was full or part.** Choose how it shows (**When armed at the keypad, show as**).
- **Part arms work for area 1 only.**
- **After arming or disarming from Home Assistant**, the panel holds back its other updates for about 30 seconds. Nothing is lost, but sensors update late.
- **The UDL code is only needed for arming, disarming and the readings.** Without it you get the zone sensors, and the alarm shows its state without arm buttons.
- **Some panels only take arm and disarm commands in the Texecom Connect form** over this port (seen on a Premier Elite 168, V6.06.03). Home Assistant tries that last, in the same session, so it can take about 10 seconds to arm or disarm.

## Set up the COM port

At the keypad: engineer code → *UDL/Digi Options* → 8 *Com Port Setup* → choose the port → *No* to edit → 8 *Crestron System* → *Yes* to save. (Or ask your installer, or use Wintex.)

## Add it to Home Assistant

**Settings → Devices & services → Add integration → Texecom → Crestron**, then:

- **Over the network**: the adapter's IP address and port (often 23), the UDL code (optional), and how many zones and areas you have. Home Assistant checks the panel answers before it saves.
- **Serial cable**: the device path (a `/dev/serial/by-id/…` path survives restarts), the speed set for the COM port (often 19200), the UDL code (optional), and the zone and area counts.

Then choose your arm modes, as for Texecom Connect.

## Options

**Arm buttons**, **Home Assistant alarm code**, **Names for keypad users** and **Create or refresh the Alarm dashboard** work as for Texecom Connect ([Options](using.md#options)). Crestron has no notifications, panel clock or re-reading of zones, and adds:

| Option | What it does |
|---|---|
| **When armed at the keypad, show as** | Away, Home or Night: Crestron doesn't say which |
| **Check the panel every** | How often Home Assistant asks the panel whether it's armed (it corrects missed events, and reconnects if the panel goes quiet). 0 turns it off |

---

[← Back to the README](../../README.md)
