# Architecture

The integration is built from **compartments**: small modules with one job each, a clear boundary, and their own tests. A fix or feature should touch one compartment (and its test file), not the whole codebase.

**On this page:** [The three layers](#the-three-layers) · [Every file and its job](#every-file-and-its-job) · [The contracts](#the-contracts) · [How things flow](#how-things-flow) · [How a driver is put together](#how-a-driver-is-put-together)

## The three layers

```
 Home Assistant                                    (layer 3: Home Assistant glue)
 ┌───────────────────────────────────────────────────────────────────────────┐
 │ setup screens   config_flow.py, flows/                                    │
 │ setup/unload    __init__.py  ──builds──▶  factory.py                      │
 │ entities        entity.py, alarm_control_panel.py, binary_sensor.py,      │
 │                 sensor.py                                                 │
 │ notices         notifications.py, issues.py, repairs.py                   │
 │ extras          dashboard.py, diagnostics.py                              │
 └───────────────▲───────────────────────────────────────────┬───────────────┘
                 │ reads zones, areas, extra; listeners;     │ arm(), disarm(),
                 │ on_event callbacks                        │ start(), stop()
 ┌───────────────┴───────────────────────────────────────────▼───────────────┐
 │ TexecomPanel interface: panel.py           (the contract between 2 and 3) │
 ├───────────────────────────────────────────────────────────────────────────┤
 │ Texecom Connect driver   connect/panel.py + discovery.py, events.py,      │
 │                          conditions.py, clock.py; connect/client.py       │
 │ Crestron driver          crestron/panel.py + crestron/connection.py       │
 │                                                    (layer 2: panel drivers)│
 ├───────────────────────────────────────────────────────────────────────────┤
 │ Byte formats only        connect/protocol.py, crestron/protocol.py        │
 │                                                    (layer 1: protocol)     │
 └───────────────────────────────────────────────────────────────────────────┘
```

| Layer | Job | May import Home Assistant? |
|---|---|---|
| **1. Protocol** | Byte formats only: frames, checksums, commands, decoding messages and log events. No state, no network | No |
| **2. Panel drivers** | Talk to the panel: connection, reconnecting, discovery, keeping zones and areas up to date, interpreting events, arm/disarm. Expose one interface: `TexecomPanel` | No |
| **3. Home Assistant glue** | Setup screens, entities, events on Home Assistant's bus, notifications, Repairs, dashboard, diagnostics. Only uses the `TexecomPanel` interface, except where it builds a driver (`factory.py`) or checks a connection during setup (`flows/validation.py`: `probe` and its errors). One known exception to fix: `binary_sensor.py` imports `MAINS_FAULTS` from `connect/protocol.py` | Yes |

Each layer only uses the one below it. Layers 1 and 2 never import Home Assistant, so the driver tests run without it (and CI checks that: the driver tests run in an environment with no Home Assistant installed).

## Every file and its job

All paths are under `custom_components/texecom/`.

### Layer 1: protocol

| File | Job |
|---|---|
| `connect/protocol.py` | Texecom Connect framing (`t` frames, CRC8), command numbers, decoding zone/area/user/log messages, area flags, panel identification, power, clock; the tamper and fault log-type names |
| `crestron/protocol.py` | Crestron text lines (`"Z0071` etc.), binary UDL (Wintex) frames, part-arm frames, area bitmasks |

### Layer 2: panel drivers

| File | Job |
|---|---|
| `panel.py` | **The shared model and the contract.** `TexecomPanel` (base class for both drivers), `PanelZone`, `PanelArea`, `PanelInfo`, the area states (Home Assistant's alarm states), `nice_name()`, `PanelError`. Listeners, connection state and the 3-minute "keep showing the last state" grace |
| `connect/client.py` | One Texecom Connect session: TCP, login, event subscription, sending commands (one at a time, resent on timeout), the keep-alive, reads (zone states, area flags, power, clock, keypad text) and arm/disarm/reset commands |
| `connect/panel.py` | `ConnectPanel`: keeps one session open and reconnects with back-off; the start-up reads; zone and area state (`refresh`, `_apply_area`, the mode-switch grace); arm/disarm; diagnostics |
| `connect/discovery.py` | Reading the panel's layout: `discover()` (identity, zones, areas), `probe()` (a one-off login for setup, patient with a busy SmartCom), and re-reading over the open session (`RediscoveryMixin`) |
| `connect/events.py` | What the panel's messages mean (`EventsMixin`): zone, area and user messages; the event log (zone alarms and the zone that set the alarm off, failed arms, part arms, engineer programming ending) |
| `connect/conditions.py` | Tampers that aren't zones, faults from the log, and mains power worked out from the power readings (`ConditionsMixin`) |
| `connect/clock.py` | The panel clock (`ClockMixin`): checked on connecting, set once a day with clock sync on, shown in diagnostics |
| `crestron/connection.py` | The Crestron connection (`ConnectionMixin`): opening the port (network or serial), reading and reconnecting, the ASTATUS poll, sending commands and the UDL session for arming |
| `crestron/panel.py` | `CrestronPanel`: turning lines into zone and area states (event coalescing, ASTATUS corrections), arm/disarm |

### Layer 3: Home Assistant glue

| File | Job |
|---|---|
| `__init__.py` | Setting up and unloading a config entry; `TexecomConfigEntry`. Reloads the entry when its options change |
| `factory.py` | Builds the driver for an entry (`create_panel`) and wires its callbacks: `texecom_event` on the bus, reauth when the UDL code is refused, reload when the panel's zones change. Also the stored form of the layout (`layout_to_data`, `zones_from_data`, `areas_from_data`) |
| `entity.py` | What every entity shares: availability (stays available through short drops), device info, predictable entity IDs (`*.texecom_<name>`), putting zones in matching rooms |
| `alarm_control_panel.py` | The alarm (one per area): state, changed by, arm/disarm services, the optional Home Assistant code |
| `binary_sensor.py` | Zones, zone tampers, the panel's Tamper, Problem, Mains power and Panel connection |
| `sensor.py` | Voltages and currents, Keypad display |
| `notifications.py` | The "Alarm not set" notification |
| `issues.py` | Repairs notices: panel clock wrong, panel unreachable |
| `repairs.py` | The Fix for a wrong clock (turns clock sync on) |
| `dashboard.py` | Builds the Alarm dashboard from Home Assistant's own cards |
| `diagnostics.py` | The diagnostics download (codes and address removed) |
| `config_flow.py` | `TexecomConfigFlow`: the first menu, the arm modes step both protocols end on, and the flow class Home Assistant registers |
| `flows/connect.py` | The Texecom Connect setup screen |
| `flows/crestron.py` | The Crestron setup screens (network, serial) |
| `flows/reauth_reconfigure.py` | Reauth (a new UDL code) and reconfigure (a new address or code) |
| `flows/options.py` | The options screen |
| `flows/validation.py` | Checks and form fields the screens share: UDL format, a Connect login, a Crestron port answering, a serial device opening, the arm-mode fields |
| `const.py` | Config keys and defaults |
| `strings.json` | **All text on screen.** `translations/en.json` must be an identical copy |
| `manifest.json` | Integration metadata, including the version |
| `brand/` | Icons shown in Home Assistant and HACS |

### Tests

Driver tests (`tests/`, no Home Assistant):

| File | Covers |
|---|---|
| `tests/test_connect.py` | Connect protocol decoding and the Connect driver, against `tests/fake_connect_panel.py` |
| `tests/test_crestron.py` | Crestron protocol and driver, against `FakeCrestronPort` (in the same file) |

Home Assistant tests (`tests/ha/`): one file per module, named after it.

| Module | Test file |
|---|---|
| `__init__.py`, `factory.py` | `test_init.py` (what each kind of entry sets up; a Crestron entry end to end) |
| `alarm_control_panel.py` | `test_alarm_control_panel.py` |
| `binary_sensor.py` | `test_binary_sensor.py` |
| `entity.py` | `test_entity.py` |
| `notifications.py` | `test_notifications.py` |
| `issues.py` | `test_issues.py` |
| `repairs.py` | `test_repairs.py` |
| `diagnostics.py` | `test_diagnostics.py` |
| `dashboard.py` | `test_dashboard.py` |
| `config_flow.py` | `test_config_flow.py` |
| `flows/<name>.py` | `test_flows_<name>.py` |

`tests/ha/common.py` has the shared helpers (`setup_connect`, `wait_for`, `start`…) and `tests/ha/conftest.py` the fixtures (`fake`: a simulated Connect panel on localhost; short timings). See [Testing](testing.md).

## The contracts

These are relied on by people's setups and automations, or by the other layer. **Don't change them without a plan** (and say so in the pull request):

1. **The `TexecomPanel` interface** (`panel.py`), which is all the Home Assistant code may use from a driver:
   - data: `zones` and `areas` (dicts of `PanelZone` / `PanelArea`), `info` (`PanelInfo`), `part_arms`, `extra` (`"power"`, `"display"`, `"faults"`, `"tampers"`), `connected`, `disconnected_since`, `recently_connected`, `can_control`, `offered_modes`
   - methods: `start()`, `stop()`, `arm(area, mode)`, `disarm(area)` (both raise `PanelError`), `add_listener()`, `diagnostics()`, and `last_error` on both drivers; Connect also has `async_diagnostics()` and `async_rediscover()`
   - callbacks the factory passes in: `on_event(type, details)`, and for Connect `on_layout_changed`, `on_auth_failed`, `on_clock_drift`
2. **`texecom_event` types and fields**: `zone_alarm` (`zone`, `zone_name`, `tamper`), `arm_failed` (`zone`, `zone_name`, `areas`), `user` (`user`, `method`), `tamper`/`tamper_cleared` and `fault`/`fault_cleared` (`source`, `log_type`), plus `entry_id` on all of them. Adding a field is fine; renaming or removing one breaks automations.
3. **Entity unique IDs and entity IDs.** Unique IDs are `<entry id>_<key>` (keys: `area_<n>`, `zone_<n>`, `zone_<n>_tamper`, `connection`, `system_tamper`, `problem`, `mains`, `panel_voltage`, `battery_voltage`, `panel_current`, `battery_current`, `display`); entity IDs are `<platform>.texecom_<name>`. Changing either makes Home Assistant create new entities and orphans the old ones (and breaks dashboards and automations).
4. **The stored config entry** (`const.py` keys): data `protocol`, `host`, `port`, `udl`, `info`, `zones`, `areas` (Connect); `connection`, `serial_device`, `baud_rate`, `zone_count`, `area_count` (Crestron); options `night_part_arm`, `home_part_arm`, `keypad_arm_mode`, `alarm_code`, `code_arm_required`, `time_sync`, `status_poll`. Change the shape only with a version bump (`TexecomConfigFlow.VERSION` / `MINOR_VERSION`), an `async_migrate_entry` in `__init__.py`, and a test that loads an entry stored the old way.
5. **Step ids and translation keys**, which tie `strings.json` to the screens.

## How things flow

**A door opens.** The panel sends a zone message → `connect/client.py` reads the frame (`protocol.py` decodes it) → `EventsMixin._on_message` (`connect/events.py`) → `TexecomPanel.set_zone` (`panel.py`) → `notify()` → every entity's listener writes its state to Home Assistant.

**You arm Night from Home Assistant.** `alarm_control_panel.py` (checks the optional code) → `ConnectPanel.arm(area, "night")` (`connect/panel.py`: picks the part arm, disarms first when switching mode) → `client.arm()` → the panel ACKs and starts its exit delay → area messages ("in exit", then "part armed") → `_apply_area` → the alarm shows *Arming…*, then *Armed: Night*.

**The alarm goes off.** Area message "in alarm" and the zone's *alarmed* flag (and later a log entry) → `_apply_area` / `_credit_alarm_zone` (`connect/events.py`) set *changed by* to the zone → `on_event("zone_alarm", …)` → `factory.fire` → `texecom_event` on Home Assistant's bus. The SmartCom then drops the session for about a minute to report the alarm; `connect/panel.py` reconnects, and entities keep their last state meanwhile (`panel.py`'s grace, `entity.py`'s `available`).

**Starting up.** `__init__.async_setup_entry` → `factory.create_panel` builds the driver from the stored entry → the platforms add their entities → `panel.start()` connects in the background (`ConnectPanel._run`: login, start-up reads, the clock check, then wait; reconnect with back-off on any failure).

## How a driver is put together

Each driver class is assembled from **mixins**, one per compartment:

```python
class ConnectPanel(RediscoveryMixin, EventsMixin, ConditionsMixin, ClockMixin, TexecomPanel): ...


class CrestronPanel(ConnectionMixin, TexecomPanel): ...


class TexecomConfigFlow(ConnectSteps, CrestronSteps, ReauthReconfigureSteps, ConfigFlow, domain=DOMAIN): ...
```

- Each mixin holds the methods for one job, in its own module.
- The state lives on the panel object and is set up in its `__init__`. Each mixin lists the attributes it uses at the top of the class ("Attributes of the panel these methods use"): that's its boundary. If a change needs a new attribute, add it to `__init__` and to that list.
- Method names are unique across mixins, so nothing overrides anything by accident. Keep it that way: before adding a method, check the name isn't used in another mixin or in `TexecomPanel`.

Constants live with the code that uses them (for example `MAINS_VOLTAGE` in `conditions.py`, `SWITCH_GRACE` in `connect/panel.py`).

---

[Developer guide](README.md) · [Making changes →](making-changes.md)
