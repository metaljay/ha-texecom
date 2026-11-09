# Testing

Two test suites, a linter, and two validators. CI runs all of them on every push (`.github/workflows/validate.yml`).

**On this page:** [Run everything](#run-everything) · [The test suites](#the-test-suites) · [The simulated panels](#the-simulated-panels) · [Writing a test](#writing-a-test) · [hassfest locally](#hassfest-locally) · [On a test Home Assistant](#on-a-test-home-assistant) · [On a real panel](#on-a-real-panel)

## Run everything

The two suites need different Pythons, so use two virtual environments:

```bash
# Driver tests: Python 3.13, no Home Assistant
python3.13 -m venv .venv-driver
.venv-driver/bin/pip install pytest pytest-asyncio ruff "pyserial-asyncio-fast>=0.11"
.venv-driver/bin/pytest tests --ignore=tests/ha
.venv-driver/bin/ruff check .
.venv-driver/bin/ruff format --check .

# Home Assistant tests: Python 3.14, inside Home Assistant 2026.10.2
python3.14 -m venv .venv-ha
.venv-ha/bin/pip install "pytest-homeassistant-custom-component==0.13.373" "pyserial-asyncio-fast>=0.11"
.venv-ha/bin/pytest tests/ha
```

(`.venv*` folders are ignored by git.) On a Mac, Python 3.14 comes from Homebrew (`brew install python@3.14`); `uv` works too (`uv python install 3.14`).

Run one compartment's tests while you work, e.g. `pytest tests/ha/test_flows_options.py`, then everything before you commit.

`ruff format` also formats Python code blocks inside Markdown files (`docs/`, `README.md`), so run it after editing docs too.

> Don't run `pytest tests` (both suites at once) inside the Home Assistant environment: Home Assistant's test plugin blocks network sockets, which the driver tests need. Run the two suites separately, as above and as CI does.

CI runs the Home Assistant tests on three versions of Home Assistant:

| Which | When | Where it's set |
|---|---|---|
| **Pinned** (the release we test on) | Every push | `validate.yml`, `AGENTS.md` and this page: move all three to a newer release now and then |
| **Oldest supported** | Every push | `validate.yml`; the same version is the minimum in `hacs.json`, the blueprints' `min_version`, the README and the website (`site/index.html`): raise them together |
| **Newest**, betas included | Every Monday, or by hand (*Actions → Validate → Run workflow*) | Not pinned: whatever is newest that week |

If a Monday run fails, CI opens an issue, *Weekly check failed* (or comments on the one already open). Fix it on `main`, then release a patch so users who update Home Assistant aren't left with a broken alarm.

## The test suites

| Suite | Where | What it covers |
|---|---|---|
| **Driver tests** | `tests/test_connect.py`, `tests/test_crestron.py` | Layers 1 and 2: decoding, and each driver against a simulated panel over real sockets. Fast, and need no Home Assistant |
| **Home Assistant tests** | `tests/ha/` | Layer 3: the integration running inside a real Home Assistant (setup screens, options, entities, services, events, notifications, the activity list, Repairs, diagnostics, the dashboard, the blueprints), against the same simulated panels |

The Home Assistant tests have one file per module (see [Architecture](architecture.md#tests)). `tests/ha/common.py` holds shared helpers:

| Helper | Does |
|---|---|
| `setup_connect(hass, fake, options=..., data=...)` | Adds a Connect entry for the simulated panel, sets it up, waits until connected; returns the entry |
| `wait_for(predicate, timeout)` | Waits until something is true (state changes arrive asynchronously) |
| `start(hass, "connect")` | Starts the setup flow and picks a menu entry |
| `after_progress(hass, result)` | Moves a flow on past a progress screen (*Connecting to your panel…*) once its work has finished. Pass `hass.config_entries.options` as the third argument for the options flow |
| `free_port()` | A port nothing listens on (for "couldn't connect" tests) |
| `state(hass)` / `ALARM` | The alarm entity's state, and its entity ID (`alarm_control_panel.texecom_house`) |

`tests/ha/conftest.py` provides the `fake` fixture (a simulated Connect panel with the demo zones), `no_setup` (stops setup flows at the entry, without connecting), and shortens the timings so tests run in seconds.

## The simulated panels

**`tests/fake_connect_panel.py`: `FakeConnectPanel`**, a Premier Elite 24 with one area (or more), speaking Texecom Connect on localhost. It answers logins, reads, arm/disarm/reset, and can be told to:

| Call or setting | Simulates |
|---|---|
| `set_zone(n, bits)` | A zone changing (`0x01` active, `0x02` tamper, `0x11` active and alarmed, `0x20` bypassed) |
| `set_area(state, part_arm=None, area=1)` | The area changing (`0` disarmed, `1` exit, `2` entry, `3` armed, `4` part armed, `5` alarm) |
| `FakeConnectPanel(areas={1: "HOUSE", 2: "GARAGE"}, zones=with_garage_area(TEST_ZONES))` | **More than one area.** A zone entry can carry an area bitmap as a fourth item (area 1 when left out); `with_garage_area()` puts the Garage in area 2. Each area has its own state, flags and Ready, and arm/disarm act on the areas in the request's bitmap. `ident` changes the panel identification (e.g. `b"Elite 12     V6.03.02LS1"`) |
| `send_zone_alarm(n)` | Zone *n*'s alarm log entry, typed as a real panel types it (1 for an entry/exit zone, 3 for an interior one) |
| `send_user(n)` | A user entering a code at a keypad (which also silences the internal alarm) |
| `open_tamper(type, logged=True)` | A tamper opening (`60` the lid, `62` a detector's cover): logged, and while disarmed the internal alarm goes off (area flags 44 and 62) until a code is entered. `logged=False` leaves the log out, as a real panel once did |
| `internal_alarm` | Area flags 44 *Internal Alarm* and 62 *Speaker Mimic* on or off |
| `send_log(type, group, parameter, areas=1)` | An event-log entry (e.g. `send_log(85, 0, 3)`: arm failed, zone 3 active) |
| `drop_all(alarm=True)` | Hanging up, as when the SmartCom reports an alarm |
| `busy_after_alarm = seconds` (`report_after`) | Reporting an alarm as a real SmartCom does: `report_after` seconds (1.5) after the disarm that follows an alarm it hangs up, then turns logins away for `busy_after_alarm` seconds (about 120 on a real one) with frames the driver doesn't know and one with a bad CRC, then closes. `0` (the default): never |
| `exit_delay` | Seconds from an arm to armed; `0` arms at once, with no exit time |
| `nak_next[command] = n` | Refusing the next *n* of a command (a busy panel) |
| `ignore_next[command] = n` | Not answering the next *n* |
| `on_battery = True` / `power_override = bytes` | Power readings on battery, or any raw reading |
| `clock_offset` / `clock_raw` | The panel's clock wrong, or holding an impossible date |
| `ready` | The Ready flag (16): `None` (the default) sets it while disarmed with no zone open; `True`/`False` force it |
| `system_flags` | The 8 bytes the panel returns for *get system flags* |
| `display` | The keypads' text (command `display TEXT`); `None`: *Premier Elite* and the time |
| `udl` | A different UDL code (for reauth tests) |
| `commands` / `connections` / `clock_set_to` | What it received, for assertions |

It also runs on its own, so a test Home Assistant can connect to it:

```bash
python tests/fake_connect_panel.py 10001 --demo          # friendly zone names, zones that wander
python tests/fake_connect_panel.py 10001 --clock-reset   # its clock says 31 Oct 2023
python tests/fake_connect_panel.py 10001 --demo --commands   # and take commands (below)
python tests/fake_connect_panel.py 10001 --demo --two-areas  # the Garage in a second area, GARAGE
```

With `--commands` you type what the panel should do next: `lid open`, `aux open unlogged`, `mains off`, `user 3`, `armfail 4`, `zone 4 alarm`, `area alarm`, `area 2 armed`, `drop`, `busy 120`… (it prints the full list, `COMMANDS` in the file). `mains off` logs AC Fail at once and once more 301 seconds later, as the real panel did (`ac_fail_repeat` on the fake; 0 for never); `mains on` cancels the repeat. That's how to try notifications, the activity list and the blueprints on a test Home Assistant without touching a real alarm. To send commands while it runs in the background, feed it from a file:

```bash
touch fake.in
tail -n0 -f fake.in | python tests/fake_connect_panel.py 10001 --demo --commands > fake.log 2>&1 &
echo "lid open" >> fake.in      # then look at fake.log for "ok: lid open"
```

### Someone else's panel, from their diagnostics

A diagnostics file a user shares can become the simulated panel: their model, firmware, zones (names, types, areas), areas and the states they were in. The command `replay` then sends the messages their panel sent (the last 200), in order:

```bash
python tests/fake_connect_panel.py 10001 --from their-diagnostics.json --commands
echo "replay" >> fake.in        # real time; "replay 10" ten times as fast, "replay 0" all at once
```

Gaps longer than 5 seconds are cut to 5. In a test, use `FakeConnectPanel.from_diagnostics(load_diagnostics(path))` and `await fake.replay(speed=0)`.

**Every file in `tests/diagnostics/` is a test** (`test_a_shared_diagnostics_file_replays_to_the_same_picture`): the driver must read the same zones and areas, receive the same messages, and end with the same zone and area states. It checks that the driver still handles that panel the way it did when the file was made, so a change can't quietly break a panel we don't own. It doesn't prove the panel's messages are understood correctly: that still needs a timeline from the owner. To add one:

**The `real-*.json` files are one session on a real Elite 24**, a step each (2 Nov 2026), with millisecond times: arms from Home Assistant and the keypad, entry, both kinds of alarm, tampers, mains off. Each also holds what really happened (`expect`): `test_a_recorded_session_on_a_real_panel_goes_the_same_way` sends it through the driver and checks the same area states and events come out, so a change that breaks how the real panel is handled fails here. In these tests the simulated panel only acknowledges arm and disarm commands (`scripted`), as the recording brings its answers. They also hold `recent_reads`: what the real panel answered when asked for area flags (and, in the mains-off step, power), by time. A replay makes the simulated panel answer the same from each time on, instead of from its own model (`test_a_recorded_session_answers_readings_as_the_real_panel_did`). A file without `recent_reads` replays as before. Once a replay has set an area's flags from the recording, they stay that way for that simulator: typed commands such as `area armed` no longer change them, so start a fresh simulator to go back to the model. With readings between messages, a replay at normal speed can pause a little longer than 5 s across a long gap.

1. Ask the person first (their issue or thread), and only use a file they posted publicly.
2. Make it anonymous: replace zone, area and user names that say anything about them (*Smith's Room* → *Bedroom 2*) in the `zones` and `areas` lists **and** in the `read` text of the messages. Keep the `raw` bytes as they are (they carry no names or codes), and check that the address and codes still read `**REDACTED**`.
3. Name it after the panel, e.g. `elite24-v6.06-comwifi.json`, and run the driver tests.

`sample-elite48-two-areas.json` is made up (by this driver against the simulated panel), as an example.

**`FakeCrestronPort`** (in `tests/test_crestron.py`) answers like a COM port set to Crestron: `send(line)` sends a line (e.g. `'"Z0021'`), `armed` sets the ASTATUS answer, `login_ok=False` / `text_error=True` mimic a panel that ignores the UDL login and refuses text commands.

## Writing a test

- **Driver behaviour** → `tests/test_connect.py` or `tests/test_crestron.py`: build a panel with `make_panel(fake, ...)`, make the fake do something, `wait_for` the result. Always `await panel.stop()` in a `finally`.
- **A screen that talks to the panel** shows a progress screen first: `result = await after_progress(hass, result)` to get the next screen.
- **A blueprint** → `tests/ha/test_blueprints.py`: `install()` it, `use()` it with inputs, then drive the fake and check the outcome.
- **Home Assistant behaviour** → the module's file in `tests/ha/`: `entry = await setup_connect(hass, fake)`, act (a service call, a fake event), `wait_for` the state, and finish with `assert await hass.config_entries.async_unload(entry.entry_id)`.
- **What an entry saves** (data or options), **entity unique IDs** → `tests/ha/test_upgrade.py` holds setups frozen exactly as v0.1.0 saved them, and checks today's code loads them unchanged, with the same IDs. Never edit the frozen sets to make a test pass: a failure there means existing users would break, so add `async_migrate_entry` instead. When a release starts saving something new, add a frozen set for it.
- Name tests after the behaviour, in plain words: `test_alarm_memory_doesnt_blame_a_zone_for_a_later_alarm`.
- Prefer a test that fails without your change. Run it before the fix to see it fail.

## hassfest locally

CI runs Home Assistant's `hassfest` validator (manifest, `strings.json`, config flow). To run it yourself you need Home Assistant's source at the matching release:

```bash
git clone --depth 1 --branch 2026.10.2 https://github.com/home-assistant/core ha-core
python3.14 -m venv .venv-hassfest
.venv-hassfest/bin/pip install -r ha-core/requirements.txt
cd ha-core && ../.venv-hassfest/bin/python -m script.hassfest --action validate --integration-path ../custom_components/texecom
```

It's optional: CI does it anyway.

## On a test Home Assistant

Before a release, the owner's test Home Assistant (a separate Docker container) runs the branch against the simulated panel and, when the change touches the driver, the real panel:

1. Copy `custom_components/texecom` into the test instance's `custom_components`, and restart it.
2. Start the simulated panel (above) where the test instance can reach it, and add a Texecom entry pointing at it.
3. Try what changed, as a user would. Download diagnostics, and check the log (`custom_components.texecom: debug`).

The owner's private notes have the addresses and commands for their setup.

## On a real panel

See the [live test plan](../testing/live-test-plan.md), including its safety rules: **never arm or disarm a real panel unless its owner is there and has agreed.**

---

[← Making changes](making-changes.md) · [What we know about the panel →](protocol.md)
