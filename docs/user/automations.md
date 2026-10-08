# Automations

The alarm and every zone work in Home Assistant's automations like any other device. The most useful automations come ready-made (Home Assistant calls them *blueprints*): click a button, pick your alarm, save.

**On this page:** [Arm automatically when everyone leaves](#arm-automatically-when-everyone-leaves) · [Or ask me first](#or-ask-me-first) · [Tell me about the alarm on my phone](#tell-me-about-the-alarm-on-my-phone) · [Events](#events) · [More ideas](#more-ideas)

## Arm automatically when everyone leaves

[![Open your Home Assistant and import the "arm when everyone leaves" blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fmetaljay%2Fha-texecom%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Ftexecom%2Farm_when_everyone_leaves.yaml)

1. Click the button above, then **Preview** and **Import blueprint**.
2. Go to **Settings → Automations & scenes → Blueprints**, and click **Texecom: arm when everyone leaves**.
3. Choose your **Alarm**. Leave **Who's home** at **Home** if everyone's phone runs the Home Assistant app. **Save** it.

It sets the alarm to **Away** when nobody has been home for 5 minutes, as long as it's off. It never disarms: you do that at the keypad as usual, or in Home Assistant with the optional alarm code. That's safer than Apple Home's "disarm when I arrive", which trusts a phone's location alone: a glitch, or a stolen unlocked phone, would open the house.

- **Who's home?** **Home** counts the people whose phones run the Home Assistant app. You can choose a person, a group of people, or a toggle instead (for example an *Occupancy* toggle that Apple Home switches when the last person leaves).
- **The wait** (5 minutes to start with) stops a quick trip to the bins from arming the house. The exit delay still runs as usual.
- **Restarts are safe**: while Home Assistant restarts, or a phone's location is unknown, the house doesn't count as empty.
- **If someone is still inside**, the arm fails at the end of the exit time (the panel sounds its "fail to set" warning), and Home Assistant shows an **Alarm not set** notification naming the zone.
- **Then also** lets you add something, such as a notification to your phone saying the alarm is set.
- **We suggest not adding an automatic disarm.** If you do, require something besides phone location (for example the front door being unlocked with a code).
- **Rather decide each time?** Use [Or ask me first](#or-ask-me-first) instead.

<details>
<summary>Prefer to write it yourself? The same thing in YAML</summary>

Go to **Settings → Automations & scenes → Create automation → Create new automation**, open **⋮ → Edit in YAML**, paste this, and change the entity names to yours:

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
mode: single
```

</details>

## Or ask me first

[![Open your Home Assistant and import the "ask to set the alarm when everyone leaves" blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fmetaljay%2Fha-texecom%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Ftexecom%2Fask_when_everyone_leaves.yaml)

The same idea, but your phone asks first: when nobody has been home for a few minutes and the alarm is off, it shows **Set the alarm?** with a **Set the alarm** button. Only that tap sets it to **Away**.

1. Click the button above, then **Preview** and **Import blueprint**.
2. Go to **Settings → Automations & scenes → Blueprints**, and click **Texecom: ask to set the alarm when everyone leaves**.
3. Choose your **Alarm**, your **Phone** (it needs the Home Assistant app) and **Who's home**, and **Save** it.

- **The button works for 30 minutes** (you can change that), so an old notification can't set the alarm hours later.
- **If someone comes home** or sets the alarm meanwhile, tapping it does nothing.

## Tell me about the alarm on my phone

[![Open your Home Assistant and import the "tell me about the alarm" blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fmetaljay%2Fha-texecom%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Ftexecom%2Falarm_alerts.yaml)

1. Click the button above, then **Preview** and **Import blueprint**.
2. Go to **Settings → Automations & scenes → Blueprints**, and click **Texecom: tell me about the alarm**.
3. Choose your **Alarm** and your **Phone** (it needs the Home Assistant app), untick anything you don't want to hear about, and **Save** it.

Your phone then gets a notification:

| When | It says |
|---|---|
| The alarm goes off | **🚨 Alarm!** *House Alarm is going off: set off by Kitchen.* |
| The alarm couldn't arm | **Alarm not set** *Front Door was still active when the exit time ended, so the alarm didn't arm.* |
| The panel loses its mains power, and gets it back | **Alarm panel on battery** · **Alarm panel mains back** |
| A tamper opens | **Alarm tamper** *The alarm panel reports a tamper: Panel Box Tamper.* |

All four work with **Texecom Connect** (a SmartCom or ComIP). With [Crestron](crestron.md) only *the alarm goes off* works, and it can't name the zone.

<details>
<summary>Prefer to write it yourself? A notification naming the zone, in YAML</summary>

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

</details>

## Events

Besides its states, the integration fires a `texecom_event` for things that happen. They show in the alarm's [activity list](using.md#the-activity-list), and you can use them in your own automations with an **Event** trigger (event type `texecom_event`, and the `type` you want):

| `type` | When | Data |
|---|---|---|
| `zone_alarm` | A zone set the alarm off | `zone`, `zone_name`, `tamper` |
| `arm_failed` | Arming failed because a zone was active when the exit time ended (one event per zone). Home Assistant also shows an **Alarm not set** notification naming the zones; it clears once the alarm arms | `zone`, `zone_name`, `areas` |
| `user` | Someone entered a code or used a tag at a keypad | `user`, `method` |
| `tamper` / `tamper_cleared` | A tamper that isn't a zone, e.g. *Panel Box Tamper* (the lid) or *Auxiliary Tamper* (a detector on the shared tamper circuit), and when it's put right | `source`, `log_type` |
| `fault` / `fault_cleared` | A fault such as *AC Fail* (mains off), *Low Battery* or *Fail to Communicate*, and when it clears | `source`, `log_type` |

Every event also has `entry_id`, which says which panel it came from if you have more than one. All but `user` need **Texecom Connect**.

## More ideas

- 💡 **Lights on** when the hallway zone sees movement after dark (zones work as motion or door sensors in any automation).
- 📟 **Keypad message**: trigger when **Keypad display** changes to something like *System Alerts!*.
- 🔋 **Mains and tamper**: Home Assistant already shows a notification for these (see [Notifications](using.md#notifications)); the blueprint above sends them to your phone too.

---

[← Apple Home](apple-home.md) · [Troubleshooting →](troubleshooting.md)
