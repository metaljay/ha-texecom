"""The automation blueprints (blueprints/automation/texecom/), run in Home
Assistant against the simulated panel."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_capture_events, async_fire_time_changed

from .common import ALARM, setup_connect, state, wait_for

BLUEPRINTS = Path(__file__).resolve().parents[2] / "blueprints" / "automation" / "texecom"
PHONE_ACTION = "  - domain: mobile_app\n    type: notify\n    device_id: !input notify_device\n"


def install(hass, tmp_path, name: str, text: str | None = None) -> None:
    """Puts a blueprint where Home Assistant looks for it (a test config folder)."""
    hass.config.config_dir = str(tmp_path)
    folder = tmp_path / "blueprints" / "automation" / "texecom"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text if text is not None else (BLUEPRINTS / name).read_text())


async def use(hass, name: str, **inputs) -> None:
    assert await async_setup_component(
        hass, "automation", {"automation": {"use_blueprint": {"path": f"texecom/{name}", "input": inputs}}}
    )
    await hass.async_block_till_done()
    assert hass.states.async_all("automation"), "the automation didn't load"


async def later(hass, **delta) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(**delta))
    await hass.async_block_till_done()


# ─── Arm when everyone leaves ───────────────────────────────────────────────


async def test_arms_when_everyone_has_left(hass, fake, tmp_path):
    install(hass, tmp_path, "arm_when_everyone_leaves.yaml")
    entry = await setup_connect(hass, fake)
    hass.states.async_set("input_boolean.occupancy", "on")
    await use(hass, "arm_when_everyone_leaves.yaml", alarm=ALARM, occupancy="input_boolean.occupancy", minutes=5)
    hass.states.async_set("input_boolean.occupancy", "off")
    await hass.async_block_till_done()
    await later(hass, minutes=3)
    assert state(hass) == "disarmed"  # not yet
    await later(hass, minutes=6)
    await wait_for(lambda: state(hass) == "armed_away")
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_coming_back_within_the_wait_doesnt_arm(hass, fake, tmp_path):
    install(hass, tmp_path, "arm_when_everyone_leaves.yaml")
    entry = await setup_connect(hass, fake)
    hass.states.async_set("zone.home", "2")
    await use(hass, "arm_when_everyone_leaves.yaml", alarm=ALARM, minutes=5)  # the Home zone, by default
    hass.states.async_set("zone.home", "0")
    await later(hass, minutes=2)
    hass.states.async_set("zone.home", "1")  # back from the bins
    await later(hass, minutes=10)
    assert state(hass) == "disarmed"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_unavailable_presence_never_arms(hass, fake, tmp_path):
    install(hass, tmp_path, "arm_when_everyone_leaves.yaml")
    entry = await setup_connect(hass, fake)
    hass.states.async_set("person.sam", "home")
    await use(hass, "arm_when_everyone_leaves.yaml", alarm=ALARM, occupancy="person.sam", minutes=1)
    hass.states.async_set("person.sam", "unavailable")  # e.g. while Home Assistant restarts
    await later(hass, minutes=5)
    assert state(hass) == "disarmed"
    hass.states.async_set("person.sam", "Work")  # in another zone: not home
    await later(hass, minutes=2)
    await wait_for(lambda: state(hass) == "armed_away")
    assert await hass.config_entries.async_unload(entry.entry_id)


# ─── Tell me about the alarm ────────────────────────────────────────────────


def alerts_for_test() -> str:
    """The alerts blueprint with its phone action swapped for an event the
    test can see (the phone action itself is Home Assistant's own)."""
    text = (BLUEPRINTS / "alarm_alerts.yaml").read_text()
    head, tail = text.split(PHONE_ACTION)
    tail = "".join("  " + line if line.strip() else line for line in tail.splitlines(keepends=True))
    return head + "  - event: texecom_test_phone\n    event_data:\n      device: !input notify_device\n" + tail


async def test_alerts(hass, fake, tmp_path):
    install(hass, tmp_path, "alarm_alerts.yaml", alerts_for_test())
    entry = await setup_connect(hass, fake)
    sent = async_capture_events(hass, "texecom_test_phone")
    await use(hass, "alarm_alerts.yaml", alarm=ALARM, notify_device="phone")

    fake.send_log(85, 0, 4)  # couldn't arm: Kitchen active at the end of the exit time
    await wait_for(lambda: len(sent) == 1)
    assert sent[0].data["title"] == "Alarm not set"
    assert sent[0].data["message"].strip() == (
        "Kitchen was still active when the exit time ended, so the alarm didn't arm."
    )

    fake.on_battery = True
    await entry.runtime_data.read_power()
    await wait_for(lambda: len(sent) == 2)
    assert sent[1].data["title"] == "Alarm panel on battery"
    fake.on_battery = False
    await entry.runtime_data.read_power()
    await wait_for(lambda: len(sent) == 3)
    assert sent[2].data["title"] == "Alarm panel mains back"

    fake.send_log(60, 11, 0, areas=0)  # the panel's lid
    await wait_for(lambda: len(sent) == 4)
    assert sent[3].data["message"].strip() == "The alarm panel reports a tamper: Panel Box Tamper."

    fake.set_area(3)
    await wait_for(lambda: state(hass) == "armed_away")
    fake.set_area(5)  # the alarm...
    fake.set_zone(4, 0x11)  # ...set off by Kitchen
    await wait_for(lambda: state(hass) == "triggered")
    await later(hass, seconds=4)
    await wait_for(lambda: len(sent) == 5)
    assert sent[4].data["title"] == "🚨 Alarm!"
    assert sent[4].data["message"].strip() == "House Alarm is going off: set off by Kitchen."
    assert sent[4].data["device"] == "phone"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_alerts_only_for_this_panel_and_as_chosen(hass, fake, tmp_path):
    install(hass, tmp_path, "alarm_alerts.yaml", alerts_for_test())
    entry = await setup_connect(hass, fake)
    sent = async_capture_events(hass, "texecom_test_phone")
    await use(hass, "alarm_alerts.yaml", alarm=ALARM, notify_device="phone", tamper=False)
    hass.bus.async_fire("texecom_event", {"type": "arm_failed", "entry_id": "another-panel", "zone_name": "Shed"})
    fake.send_log(60, 11, 0, areas=0)  # a tamper, but tamper alerts are off
    await wait_for(lambda: hass.states.get("binary_sensor.texecom_tamper").state == "on")
    await hass.async_block_till_done()
    assert sent == []
    assert await hass.config_entries.async_unload(entry.entry_id)


# ─── Ask to set the alarm when everyone leaves ──────────────────────────────

ASK = "ask_when_everyone_leaves.yaml"
ASK_PHONE_ACTION = """  - domain: mobile_app
    type: notify
    device_id: !input notify_device
    title: "Set the alarm?"
    message: "Everyone's out, and {{ state_attr(alarm, 'friendly_name') }} is off."
    data:
      actions:
        - action: "{{ button }}"
          title: "Set the alarm"
"""


def ask_for_test() -> str:
    """The blueprint with its phone notification swapped for an event the test
    can see, carrying the message and the button's action."""
    text = (BLUEPRINTS / ASK).read_text()
    assert ASK_PHONE_ACTION in text
    return text.replace(
        ASK_PHONE_ACTION,
        "  - event: texecom_test_phone\n"
        "    event_data:\n"
        "      device: !input notify_device\n"
        "      message: \"Everyone's out, and {{ state_attr(alarm, 'friendly_name') }} is off.\"\n"
        '      button: "{{ button }}"\n',
    )


async def jump(hass, **delta) -> None:
    """Moves time on without waiting for running automations to finish (this
    one waits for a tap)."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(**delta))
    await asyncio.sleep(0.05)


async def test_asks_then_sets_the_alarm_when_tapped(hass, fake, tmp_path):
    install(hass, tmp_path, ASK, ask_for_test())
    entry = await setup_connect(hass, fake)
    sent = async_capture_events(hass, "texecom_test_phone")
    hass.states.async_set("input_boolean.occupancy", "on")
    await use(hass, ASK, alarm=ALARM, notify_device="phone", occupancy="input_boolean.occupancy", minutes=5)
    hass.states.async_set("input_boolean.occupancy", "off")
    await hass.async_block_till_done()
    await jump(hass, minutes=6)
    await wait_for(lambda: len(sent) == 1)
    assert sent[0].data["message"] == "Everyone's out, and House Alarm is off."
    assert state(hass) == "disarmed"  # it only asks
    hass.bus.async_fire("mobile_app_notification_action", {"action": "SOMETHING_ELSE"})
    await asyncio.sleep(0.1)
    assert state(hass) == "disarmed"
    hass.bus.async_fire("mobile_app_notification_action", {"action": sent[0].data["button"]})
    await wait_for(lambda: state(hass) == "armed_away")
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_an_old_question_cant_set_the_alarm(hass, fake, tmp_path):
    install(hass, tmp_path, ASK, ask_for_test())
    entry = await setup_connect(hass, fake)
    sent = async_capture_events(hass, "texecom_test_phone")
    hass.states.async_set("person.sam", "home")
    await use(hass, ASK, alarm=ALARM, notify_device="phone", occupancy="person.sam", minutes=1, answer_within=5)
    hass.states.async_set("person.sam", "not_home")
    await hass.async_block_till_done()
    await jump(hass, minutes=2)
    await wait_for(lambda: len(sent) == 1)
    await jump(hass, minutes=6)  # nobody answered in time
    hass.bus.async_fire("mobile_app_notification_action", {"action": sent[0].data["button"]})
    await hass.async_block_till_done()
    assert state(hass) == "disarmed"
    assert await hass.config_entries.async_unload(entry.entry_id)
