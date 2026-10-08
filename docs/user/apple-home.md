# Apple Home

Home Assistant's built-in **HomeKit Bridge** puts the alarm (and any zones you like) in Apple's Home app, with Siri, Control Centre and Apple's critical alerts. Nothing else to install.

## Set it up

1. Choose your **Night** and **Home** buttons first (**Configure** in Home Assistant). The Home app remembers an alarm's buttons when it's added, and doesn't notice if you change them later.
2. In Home Assistant, go to **Settings → Devices & services → Add integration → HomeKit Bridge**. Choose **Alarm Control Panel** (and **Binary Sensor** if you want the zones too), and follow the pairing steps.
   Already have a HomeKit Bridge? Open its **Configure** and add `alarm_control_panel.texecom_house` (your alarm's name may differ) instead.
3. In the Home app, put the alarm in a room and turn on its notifications.

## What you'll see

| In the Home app | Comes from |
|---|---|
| **Away**, **Night**, **Home**, **Off** buttons | Away always; Night and Home only if you gave them a part arm |
| **Arming…** | The exit delay. The alarm's own page shows *Off* until the panel has armed, because that's what it is until then |
| **Triggered**, with a critical alert | The alarm going off |
| Arms and disarms made at the keypad | Show straight away (an arm once its exit delay is over) |

## Changed Night or Home?

The Home app keeps the buttons it saw first. After changing Night or Home in Home Assistant, remove the alarm from the HomeKit Bridge (**HomeKit Bridge → Configure**, untick it), save, then add it back. (HomeKit Bridge's **Reset accessory** action, in **Developer tools → Actions**, does the same in one step.) Then set its room and notifications again in the Home app.

## Codes

The Home app can't ask for a code. If you set a **Home Assistant alarm code**, the HomeKit Bridge needs it in its settings (`entity_config` → `code`), or arming from the Home app fails.

Without a code, the Home app arms and disarms freely, as it did with Homebridge. Consider letting Home Assistant [arm automatically when everyone leaves](automations.md#arm-automatically-when-everyone-leaves), and leaving disarming to the keypad.

## The name

The alarm appears as **House Alarm** (your area's name, then *Alarm*). To change it, rename it in the Home app.

---

[← Using it](using.md) · [Automations →](automations.md)
