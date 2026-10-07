# Automations

The alarm and every zone work in Home Assistant's automations like any other device. A few ideas, from the most useful down.

**On this page:** [Arm automatically when everyone leaves](#arm-automatically-when-everyone-leaves) · [Tell me when the alarm goes off](#tell-me-when-the-alarm-goes-off) · [Events](#events) · [More ideas](#more-ideas)

## Arm automatically when everyone leaves

Home Assistant can **arm** on its own when the house empties, and leave **disarming** to you: at the keypad as usual, or in Home Assistant with the optional alarm code. That's safer than Apple Home's "disarm when I arrive", which trusts a phone's location alone: a glitch, or a stolen unlocked phone, would open the house.

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
- **If someone is still inside**, the arm fails at the end of the exit time (the panel sounds its "fail to set" warning), and Home Assistant shows an **Alarm not set** notification naming the zone.
- **We suggest not adding an automatic disarm.** If you do, require something besides phone location (for example the front door being unlocked with a code).

## Tell me when the alarm goes off

This sends a notification to your phone (through the Home Assistant app) naming the zone that set the alarm off:

```yaml
alias: "Alarm: tell me when it goes off"
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
mode: queued
```

Or, without naming the zone, trigger on the alarm's state: `trigger: state`, `entity_id: alarm_control_panel.texecom_house`, `to: triggered`.

## Events

Besides its states, the integration fires a `texecom_event` for things that happen. Use them with an **Event** trigger (event type `texecom_event`, and the `type` you want):

| `type` | When | Data |
|---|---|---|
| `zone_alarm` | A zone set the alarm off (Texecom Connect) | `zone`, `zone_name`, `tamper` |
| `arm_failed` | Arming failed because a zone was active when the exit time ended (one event per zone). Home Assistant also shows an **Alarm not set** notification naming the zones; it clears once the alarm arms | `zone`, `zone_name`, `areas` |
| `user` | Someone entered a code or used a tag at a keypad | `user`, `method` |
| `tamper` / `tamper_cleared` | A tamper that isn't a zone (Texecom Connect), e.g. *Panel Box Tamper* (the lid) or *Auxiliary Tamper* (a detector on the shared tamper circuit), and when it's put right | `source`, `log_type` |
| `fault` / `fault_cleared` | A fault such as *AC Fail* (mains off), *Low Battery* or *Fail to Communicate*, and when it clears | `source`, `log_type` |

Every event also has `entry_id`, which says which panel it came from if you have more than one.

## More ideas

- 💡 **Lights on** when the hallway zone sees movement after dark (zones work as motion or door sensors in any automation).
- 🔋 **Tell me if the mains goes off**: trigger when **Mains power** turns off.
- 🔧 **Tell me about a tamper**: trigger when the panel's **Tamper** turns on; its *sources* attribute says which.
- 📟 **Keypad message**: trigger when **Keypad display** changes to something like *System Alerts!*.

---

[← Apple Home](apple-home.md) · [Troubleshooting →](troubleshooting.md)
