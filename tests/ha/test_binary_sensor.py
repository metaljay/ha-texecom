"""Zone, tamper, problem and mains sensors (binary_sensor.py), and the
events that go with them."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import async_capture_events

from .common import setup_connect, wait_for


def is_on(hass, entity_id: str) -> bool:
    return hass.states.get(entity_id).state == "on"


async def test_panel_lid_and_detector_tampers(hass, fake):
    entry = await setup_connect(hass, fake)
    events = async_capture_events(hass, "texecom_event")
    fake.send_log(60, 11, 0, areas=0)  # lid off
    fake.send_log(60, 11, 0, areas=0)  # the panel logs it again once reported
    fake.send_log(62, 11, 0, areas=0)  # a detector's cover off (shared tamper circuit)
    await wait_for(lambda: is_on(hass, "binary_sensor.texecom_tamper"))
    await wait_for(
        lambda: (
            hass.states.get("binary_sensor.texecom_tamper").attributes["sources"]
            == ["Auxiliary Tamper", "Panel Box Tamper"]
        )
    )
    fake.send_log(60, 12, 0, areas=0)
    fake.send_log(62, 12, 0, areas=0)
    await wait_for(lambda: not is_on(hass, "binary_sensor.texecom_tamper"))
    await hass.async_block_till_done()
    kinds = [(e.data["type"], e.data["source"]) for e in events]
    assert kinds == [
        ("tamper", "Panel Box Tamper"),
        ("tamper", "Auxiliary Tamper"),
        ("tamper_cleared", "Panel Box Tamper"),
        ("tamper_cleared", "Auxiliary Tamper"),
    ]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_mains_failure_and_restore(hass, fake):
    entry = await setup_connect(hass, fake)
    events = async_capture_events(hass, "texecom_event")
    fake.on_battery = True
    fake.send_log(47, 9, 0, areas=0)  # AC Fail; the panel never logs the restore
    await wait_for(lambda: not is_on(hass, "binary_sensor.texecom_mains_power"))
    assert is_on(hass, "binary_sensor.texecom_problem")
    assert hass.states.get("binary_sensor.texecom_problem").attributes["sources"] == ["AC Fail"]
    await entry.runtime_data.read_power()  # still on battery: no second event
    fake.on_battery = False
    await entry.runtime_data.read_power()
    await hass.async_block_till_done()
    assert is_on(hass, "binary_sensor.texecom_mains_power")
    assert not is_on(hass, "binary_sensor.texecom_problem")
    assert [e.data["type"] for e in events] == ["fault", "fault_cleared"]
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_a_panel_that_never_reports_current_isnt_on_battery(hass, fake):
    # 0 mA at 13.4 V: a panel that doesn't measure current, on mains
    fake.power_override = bytes([100, 99, 99, 0, 0])
    entry = await setup_connect(hass, fake)
    assert is_on(hass, "binary_sensor.texecom_mains_power")
    # ...but a clearly flat voltage with no current is a mains failure
    fake.power_override = bytes([100, 78, 79, 0, 0])  # ~12.2 V
    await entry.runtime_data.read_power()
    await hass.async_block_till_done()
    assert not is_on(hass, "binary_sensor.texecom_mains_power")
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_zone_faults_name_the_zone(hass, fake):
    entry = await setup_connect(hass, fake)
    fake.send_log(105, 20, 4, areas=0)  # Zone Masked: Kitchen
    await wait_for(
        lambda: hass.states.get("binary_sensor.texecom_problem").attributes["sources"] == ["Zone Masked: Kitchen"]
    )
    fake.send_log(105, 10, 4, areas=0)
    await wait_for(lambda: not is_on(hass, "binary_sensor.texecom_problem"))
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_zone_activity_and_bypass(hass, fake):
    entry = await setup_connect(hass, fake)
    fake.set_zone(2, 0x01)
    await wait_for(lambda: is_on(hass, "binary_sensor.texecom_hallway"))
    fake.set_zone(2, 0x20)  # secure, manually bypassed
    await wait_for(lambda: not is_on(hass, "binary_sensor.texecom_hallway"))
    assert hass.states.get("binary_sensor.texecom_hallway").attributes["bypassed"] is True
    assert await hass.config_entries.async_unload(entry.entry_id)
