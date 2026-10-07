# Testing

Two test suites, a linter, and two validators. CI runs all of them on every push (`.github/workflows/validate.yml`).

**On this page:** [Run everything](#run-everything) · [The test suites](#the-test-suites) · [The simulated panels](#the-simulated-panels) · [Writing a test](#writing-a-test) · [hassfest locally](#hassfest-locally) · [On a test Home Assistant](#on-a-test-home-assistant) · [On a real panel](#on-a-real-panel)

## Run everything

The two suites need different Pythons, so use two virtual environments:

```bash
# Driver tests: Python 3.13, no Home Assistant
python3.13 -m venv .venv-driver
.venv-driver/bin/pip install pytest pytest-asyncio ruff
.venv-driver/bin/pytest tests --ignore=tests/ha
.venv-driver/bin/ruff check .
.venv-driver/bin/ruff format --check .

# Home Assistant tests: Python 3.14, inside Home Assistant 2026.9.4
python3.14 -m venv .venv-ha
.venv-ha/bin/pip install "pytest-homeassistant-custom-component==0.13.367"
.venv-ha/bin/pytest tests/ha
```

(`.venv*` folders are ignored by git.) On a Mac, Python 3.14 comes from Homebrew (`brew install python@3.14`); `uv` works too (`uv python install 3.14`).

Run one compartment's tests while you work, e.g. `pytest tests/ha/test_flows_options.py`, then everything before you commit.

`ruff format` also formats Python code blocks inside Markdown files (`docs/`, `README.md`), so run it after editing docs too.

> Don't run `pytest tests` (both suites at once) inside the Home Assistant environment: Home Assistant's test plugin blocks network sockets, which the driver tests need. Run the two suites separately, as above and as CI does.

## The test suites

| Suite | Where | What it covers |
|---|---|---|
| **Driver tests** (34) | `tests/test_connect.py`, `tests/test_crestron.py` | Layers 1 and 2: decoding, and each driver against a simulated panel over real sockets. Fast, and need no Home Assistant |
| **Home Assistant tests** (41) | `tests/ha/` | Layer 3: the integration running inside a real Home Assistant (setup screens, options, entities, services, events, notifications, Repairs, diagnostics, the dashboard), against the same simulated panels |

The Home Assistant tests have one file per module (see [Architecture](architecture.md#tests)). `tests/ha/common.py` holds shared helpers:

| Helper | Does |
|---|---|
| `setup_connect(hass, fake, options=..., data=...)` | Adds a Connect entry for the simulated panel, sets it up, waits until connected; returns the entry |
| `wait_for(predicate, timeout)` | Waits until something is true (state changes arrive asynchronously) |
| `start(hass, "connect")` | Starts the setup flow and picks a menu entry |
| `free_port()` | A port nothing listens on (for "couldn't connect" tests) |
| `state(hass)` / `ALARM` | The alarm entity's state, and its entity ID (`alarm_control_panel.texecom_house`) |

`tests/ha/conftest.py` provides the `fake` fixture (a simulated Connect panel with the demo zones), `no_setup` (stops setup flows at the entry, without connecting), and shortens the timings so tests run in seconds.

## The simulated panels

**`tests/fake_connect_panel.py`: `FakeConnectPanel`**, a Premier Elite 24 with one area, speaking Texecom Connect on localhost. It answers logins, reads, arm/disarm/reset, and can be told to:

| Call or setting | Simulates |
|---|---|
| `set_zone(n, bits)` | A zone changing (`0x01` active, `0x02` tamper, `0x11` active and alarmed, `0x20` bypassed) |
| `set_area(state, part_arm=None)` | The area changing (`0` disarmed, `1` exit, `2` entry, `3` armed, `4` part armed, `5` alarm) |
| `send_user(n)` | A user entering a code at a keypad |
| `send_log(type, group, parameter, areas=1)` | An event-log entry (e.g. `send_log(85, 0, 3)`: arm failed, zone 3 active) |
| `drop_all(alarm=True)` | Hanging up, as when the SmartCom reports an alarm |
| `nak_next[command] = n` | Refusing the next *n* of a command (a busy panel) |
| `ignore_next[command] = n` | Not answering the next *n* |
| `on_battery = True` / `power_override = bytes` | Power readings on battery, or any raw reading |
| `clock_offset` / `clock_raw` | The panel's clock wrong, or holding an impossible date |
| `udl` | A different UDL code (for reauth tests) |
| `commands` / `connections` / `clock_set_to` | What it received, for assertions |

It also runs on its own, so a test Home Assistant can connect to it:

```bash
python tests/fake_connect_panel.py 10001 --demo          # friendly zone names, zones that wander
python tests/fake_connect_panel.py 10001 --clock-reset   # its clock says 31 Oct 2023
```

**`FakeCrestronPort`** (in `tests/test_crestron.py`) answers like a COM port set to Crestron: `send(line)` sends a line (e.g. `'"Z0021'`), `armed` sets the ASTATUS answer, `login_ok=False` / `text_error=True` mimic a panel that ignores the UDL login and refuses text commands.

## Writing a test

- **Driver behaviour** → `tests/test_connect.py` or `tests/test_crestron.py`: build a panel with `make_panel(fake, ...)`, make the fake do something, `wait_for` the result. Always `await panel.stop()` in a `finally`.
- **Home Assistant behaviour** → the module's file in `tests/ha/`: `entry = await setup_connect(hass, fake)`, act (a service call, a fake event), `wait_for` the state, and finish with `assert await hass.config_entries.async_unload(entry.entry_id)`.
- Name tests after the behaviour, in plain words: `test_alarm_memory_doesnt_blame_a_zone_for_a_later_alarm`.
- Prefer a test that fails without your change. Run it before the fix to see it fail.

## hassfest locally

CI runs Home Assistant's `hassfest` validator (manifest, `strings.json`, config flow). To run it yourself you need Home Assistant's source at the matching release:

```bash
git clone --depth 1 --branch 2026.9.4 https://github.com/home-assistant/core ha-core
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
