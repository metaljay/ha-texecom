# Instructions for AI agents

This repository is maintained through AI agents: **the owner doesn't write code.** Explain what you're going to do and why in plain English, keep each change small, and ask before anything risky or hard to undo. When the owner has to decide something, give a recommendation, not a list of options.

## What this is

**ha-texecom**: a Home Assistant custom integration (installed through HACS) for Texecom Premier Elite alarm panels, over Texecom Connect (a SmartCom or ComIP) or a COM port set to Crestron. Users set everything up in Home Assistant's screens; there's no YAML. The goal is an integration that is **stable** (keeps working through drops, restarts and panel quirks), **simple** (a couple of minutes to set up, few options, plain words), and **easy to change one compartment at a time**.

## Rules

1. **Never arm or disarm a real panel** unless the owner is present and has agreed to that test. Read-only checks are fine any time. Leave disarming to the owner.
2. **Never write a code into anything**: no UDL code, keypad code or alarm code in the repository, tests, issues, logs you post, or commits. Tests use the dummy codes already in them.
3. **Everything here is public.** No real names, email addresses, home details, Wintex files, or screenshots with personal data. Commit as `metaljay <metaljay@users.noreply.github.com>`, and check the diff for personal details before every push.
4. **Don't change the contracts without a plan** (and say so in the pull request): the `TexecomPanel` interface, the `texecom_event` types and fields, entity unique IDs and entity IDs, and the stored config entry data. Details: [Architecture → The contracts](docs/development/architecture.md#the-contracts).
5. **Layers 1 and 2 never import Home Assistant** (`connect/`, `crestron/`, `panel.py`).
6. **One kind of change per commit.** Moving code (a refactor) never goes in the same commit as a change in behaviour.
7. **Releases only when the owner asks**, as patch increments (0.2.3, 0.2.4…). See [Releasing](docs/development/making-changes.md#releasing).
8. **Write down what you learn about the panel** in [docs/development/protocol.md](docs/development/protocol.md), and capture it in the simulated panel and a test where you can.
9. **Work on `main`** unless the owner asks for a branch, and delete any branch once it's merged: the owner wants a single `main` branch to look after.

## Where things are

Three layers; each module has one job and its own test file. Code is under `custom_components/texecom/`, tests under `tests/`.

| Layer | Modules |
|---|---|
| **1. Protocol** (bytes only) | `connect/protocol.py`, `crestron/protocol.py` |
| **2. Panel drivers** | `panel.py` (the shared model and the `TexecomPanel` contract); Connect: `connect/client.py` (the session), `connect/panel.py` (connection, state, arm/disarm), `connect/discovery.py`, `connect/events.py`, `connect/conditions.py`, `connect/clock.py`; Crestron: `crestron/connection.py`, `crestron/panel.py` |
| **3. Home Assistant** | `__init__.py` (setup/unload), `factory.py` (builds the driver), `entity.py`, `alarm_control_panel.py`, `binary_sensor.py`, `sensor.py`, `notifications.py`, `issues.py`, `repairs.py`, `dashboard.py`, `diagnostics.py`, `config_flow.py` and `flows/` (setup and options screens), `strings.json` (all screen text; `translations/en.json` is an identical copy) |

The full map, the contracts and how things flow: [docs/development/architecture.md](docs/development/architecture.md). Which file and test to change for each kind of task: [docs/development/making-changes.md](docs/development/making-changes.md#where-does-my-change-go).

## How to make a change

1. Restate the request as what a user will see or be able to do. Ask if it's unclear.
2. Find the compartment ([the table](docs/development/making-changes.md#where-does-my-change-go)) and read that module and its test file.
3. Write a test that shows the problem or the new behaviour, and see it fail.
4. Change only that compartment.
5. Run that test file, then all the checks below.
6. Update `docs/user/` if users will notice, and `protocol.md` if you learned something about the panel.
7. Commit with a message that says what changed for users and why.

## Checks

```bash
# Python 3.13, no Home Assistant: lint and driver tests
pip install pytest pytest-asyncio ruff
ruff check . && ruff format --check .
pytest tests --ignore=tests/ha

# Python 3.14, separate environment: the Home Assistant tests
pip install "pytest-homeassistant-custom-component==0.13.367"
pytest tests/ha
```

Run the two test suites separately (Home Assistant's test plugin blocks the sockets the driver tests use). `ruff format` also checks Python code blocks inside Markdown files, so run it after editing docs too. CI (`.github/workflows/validate.yml`) runs these plus HACS and hassfest validation on every push. More: [docs/development/testing.md](docs/development/testing.md).

## Done means

- ruff clean, both test suites pass, CI green.
- New behaviour has a test; `strings.json` and `translations/en.json` match; the user docs describe it.
- No contract changed, or the pull request explains how and why.
- For anything touching a panel driver: tried on the owner's test Home Assistant (and read-only on the real panel) before it's released.

## Testing on the real panel

Follow the [live test plan](docs/testing/live-test-plan.md), including its safety rules. The owner's private notes (not in this repository) have the addresses and access details for their setup.
