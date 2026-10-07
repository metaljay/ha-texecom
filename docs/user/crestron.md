# Using a COM port set to Crestron

Most people should use **Texecom Connect** with a SmartCom or ComIP ([Setting it up](setup.md)). Crestron is the other way in: one of the panel's COM ports set to **Crestron System**, reached through a serial-to-network adapter (or a SmartCom/ComIP switched to Crestron), or a USB-serial cable on the Home Assistant machine.

It's useful for keeping the Texecom app on its SmartCom while Home Assistant uses a spare COM port.

## How it differs from Texecom Connect

- **Zone names can't be read.** You enter how many zones and areas you have; they appear as *Zone 1*, *Zone 2*… and you rename them in Home Assistant.
- **Only open/closed (active/clear)** is reported for zones: no tamper, and no voltages, mains or keypad display.
- **An arm at the keypad doesn't say whether it was full or part.** Choose how it shows (**When armed at the keypad, show as**).
- **Part arms work for area 1 only.**
- **After arming or disarming from Home Assistant**, the panel holds back its other updates for about 30 seconds. Nothing is lost, but sensors update late.
- **The UDL code is only needed for arming and disarming.** Without it you get the sensors, and the alarm shows its state without arm buttons.

## Set up the COM port

At the keypad: engineer code → *UDL/Digi Options* → 8 *Com Port Setup* → choose the port → *No* to edit → 8 *Crestron System* → *Yes* to save. (Or ask your installer, or use Wintex.)

## Add it to Home Assistant

**Settings → Devices & services → Add integration → Texecom → Crestron**, then:

- **Over the network**: the adapter's IP address and port (often 23), the UDL code (optional), and how many zones and areas you have. Home Assistant checks the panel answers before it saves.
- **Serial cable**: the device path (a `/dev/serial/by-id/…` path survives restarts), the speed set for the COM port (often 19200), the UDL code (optional), and the zone and area counts.

Then choose your arm modes, as for Texecom Connect.

## Options

As for Texecom Connect, plus:

| Option | What it does |
|---|---|
| **When armed at the keypad, show as** | Away, Home or Night: Crestron doesn't say which |
| **Check the panel every** | How often Home Assistant asks the panel whether it's armed (it corrects missed events, and reconnects if the panel goes quiet). 0 turns it off |

---

[← Back to the README](../../README.md)
