"""Setting up an entry: devices, entities, names, states."""

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .common import setup_connect


async def test_entities_devices_and_states(hass, fake):
    entry = await setup_connect(hass, fake)

    alarm = hass.states.get("alarm_control_panel.texecom_house")
    assert alarm is not None and alarm.state == "disarmed"
    assert alarm.attributes["friendly_name"] == "House Alarm"
    assert alarm.attributes["supported_features"] == 2 | 4  # away + night (home not used)

    front = hass.states.get("binary_sensor.texecom_front_door")
    assert front.state == "off" and front.attributes["device_class"] == "door"
    assert hass.states.get("binary_sensor.texecom_smoke_detector").attributes["device_class"] == "smoke"
    assert hass.states.get("binary_sensor.texecom_hallway").attributes["device_class"] == "motion"
    assert hass.states.get("binary_sensor.texecom_panel_connection").state == "on"
    assert hass.states.get("binary_sensor.texecom_mains_power").state == "on"
    assert hass.states.get("binary_sensor.texecom_tamper").state == "off"
    assert hass.states.get("binary_sensor.texecom_problem").state == "off"
    assert hass.states.get("sensor.texecom_keypad_display").state == "Premier Elite"
    assert float(hass.states.get("sensor.texecom_panel_voltage").state) > 13

    devices = {d.name for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)}
    assert {"Premier Elite 24 panel", "House Alarm", "Front Door", "Smoke Detector"} <= devices
    # Zone tamper sensors exist but are off by default.
    tamper = er.async_get(hass).async_get("binary_sensor.texecom_front_door_tamper")
    assert tamper is not None and tamper.disabled_by is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
